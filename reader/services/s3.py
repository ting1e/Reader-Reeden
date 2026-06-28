import json
import logging
import os

from ..models import UserSetting
from ..utils import get_file_md5, get_progress_dir, parse_s3_json
from .progress import _parse_progress_time

logger = logging.getLogger('reader')


def get_s3_config(user):
    if not user.is_authenticated:
        return None
    setting = UserSetting.objects.filter(user_id=user.id).first()
    if not setting:
        return None
    try:
        s3_dict = parse_s3_json(setting.s3_setting)
    except Exception:
        logger.warning("get_s3_config: failed to parse s3_setting", exc_info=True)
        return None
    prefix = s3_dict.get('prefix', '')
    if prefix and not prefix.endswith('/'):
        prefix += '/'
    return {
        'access_key': s3_dict.get('accessKeyId'),
        'secret_key': s3_dict.get('secretAccessKey'),
        'region': s3_dict.get('region'),
        'endpoint': s3_dict.get('endpoint'),
        'bucket': s3_dict.get('bucket'),
        'prefix': prefix,
    }


def _get_s3_client(cfg):
    import boto3
    from botocore.config import Config
    s3_config = Config(
        request_checksum_calculation='WHEN_REQUIRED',
        response_checksum_validation='WHEN_REQUIRED'
    )

    return boto3.client(
        's3',
        aws_access_key_id=cfg['access_key'],
        aws_secret_access_key=cfg['secret_key'],
        region_name=cfg['region'],
        endpoint_url=cfg['endpoint'],
        config=s3_config  # 注入兼容性配置
    )


def sync_progress_to_s3(request, book):
    """若本地进度比 S3 上的新（或 S3 无进度），则上传本地进度到 S3。"""
    if getattr(book, 'local_only', False):
        return
    cfg = get_s3_config(request.user)
    if not cfg or not book or not getattr(book, 'book_url', None):
        return
    try:
        md5_val = book.md5 or get_file_md5(book.abs_path())
    except Exception:
        logger.exception("sync_progress_to_s3 MD5 error")
        return
    local_path = os.path.join(get_progress_dir(), f'{md5_val}.json')
    if not os.path.exists(local_path):
        return
    try:
        with open(local_path, 'r', encoding='utf-8') as f:
            local_data = json.load(f)
    except Exception:
        logger.warning("sync_progress_to_s3: error loading local progress", exc_info=True)
        local_data = None
    local_time = _parse_progress_time(local_data)
    if local_time is None:
        return
    s3_key = f"{cfg['prefix']}book_progress/{md5_val}.json"
    try:
        client = _get_s3_client(cfg)
        try:
            resp = client.get_object(Bucket=cfg['bucket'], Key=s3_key)
            remote_data = json.load(resp['Body'])
            remote_time = _parse_progress_time(remote_data)
        except Exception:
            remote_data = None
            remote_time = None
        if remote_time is None or local_time > remote_time:
            if remote_data and isinstance(remote_data.get('todayStats'), dict) \
               and isinstance(local_data.get('todayStats'), dict):
                remote_devices = remote_data['todayStats'].get('devices', {})
                local_devices = local_data['todayStats'].get('devices', {})
                for dev_id, dev_stats in remote_devices.items():
                    if dev_id not in local_devices:
                        local_devices[dev_id] = dev_stats
                    else:
                        remote_seconds = dev_stats.get('readSeconds', 0)
                        local_seconds = local_devices[dev_id].get('readSeconds', 0)
                        if remote_seconds > local_seconds:
                            local_devices[dev_id] = dev_stats
                local_data['todayStats']['devices'] = local_devices
                try:
                    with open(local_path, 'w', encoding='utf-8') as f:
                        json.dump(local_data, f, ensure_ascii=False)
                except Exception:
                    logger.exception("sync_progress_to_s3: error writing merged local progress")
            with open(local_path, 'rb') as pf:
                body = pf.read()
            client.put_object(Bucket=cfg['bucket'], Key=s3_key, Body=body, ContentLength=len(body))
    except Exception:
        logger.exception("sync_progress_to_s3 upload error")
