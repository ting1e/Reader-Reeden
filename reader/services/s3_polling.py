"""Periodic discovery of progress JSONs for users without a local mount."""
import json
import logging
from pathlib import PurePosixPath

from django.contrib.auth import get_user_model
from botocore.exceptions import ClientError

from .s3 import get_s3_config
from .s3_correction import process_data

logger = logging.getLogger('reader.s3_progress')


def poll_progress(setting, seen):
    cfg = get_s3_config(get_user_model().objects.get(pk=setting.user_id))
    if not cfg:
        return
    from .s3 import _get_s3_client
    client = _get_s3_client(cfg)
    prefix = cfg['prefix'] + 'book_progress/'
    # Changing the source invalidates cached object metadata.
    source = (cfg['endpoint'], cfg['bucket'], prefix)
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=cfg['bucket'], Prefix=prefix):
        for item in page.get('Contents', []):
            key = item['Key']
            if not key.endswith('.json') or '/' in key[len(prefix):]:
                continue
            cache_key = (*source, key)
            previous = seen.get(cache_key)
            # Reuploads with unchanged ETag can still have a new modification time.
            signature = (item.get('ETag'), item.get('LastModified'))
            if previous and all(signature) and previous == signature:
                continue
            try:
                response = client.get_object(Bucket=cfg['bucket'], Key=key)
                with response['Body'] as stream:
                    data = json.load(stream)
                process_data(setting, PurePosixPath(key).stem, data,
                             modified_at=response.get('LastModified', item.get('LastModified')))
                seen[cache_key] = (response.get('ETag'), response.get('LastModified', item.get('LastModified')))
            except ClientError as exc:
                if exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 304:
                    continue
                logger.exception('S3 progress polling failed user=%s key=%s', setting.user_id, key)
            except Exception:
                logger.exception('S3 progress polling failed user=%s key=%s', setting.user_id, key)
