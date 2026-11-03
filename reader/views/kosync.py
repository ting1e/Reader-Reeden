"""KOReader KoSync HTTP API (v1)."""
import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from ..ratelimit import (
    check_login_allowed, get_client_ip, record_login_failure, reset_login_failures,
)
from ..services import kosync

logger = logging.getLogger('reader')


def _authenticate(request):
    ip = 'kosync:' + get_client_ip(request)
    if not check_login_allowed(ip):
        return None, True
    user = kosync.verified_user(request)
    if user is None:
        record_login_failure(ip)
        return None, False
    reset_login_failures(ip)
    return user, False


def _json(data, status=200):
    return JsonResponse(data, status=status, json_dumps_params={'ensure_ascii': False})


def _unauthorized():
    return _json({'message': 'Unauthorized', 'code': 2001}, status=401)


def _disabled():
    return _json({'message': 'KoSync server disabled', 'code': 2000}, status=503)


@csrf_exempt
@require_http_methods(['GET', 'HEAD'])
def healthcheck(request):
    return _json({'state': 'OK'})


@csrf_exempt
@require_http_methods(['GET'])
def auth(request):
    if not kosync.server_enabled():
        return _disabled()
    user, limited = _authenticate(request)
    if limited:
        return _json({'message': 'Too many attempts', 'code': 2001}, status=429)
    if user is None:
        return _unauthorized()
    return _json({'authorized': 'OK'})


@csrf_exempt
@require_http_methods(['POST'])
def create_user(request):
    # Accounts are managed by Reader-Reeden; KOReader registration stays closed.
    return _json({'message': 'Registration disabled', 'code': 2005}, status=402)


@csrf_exempt
@require_http_methods(['PUT'])
def progress(request):
    if not kosync.server_enabled():
        return _disabled()
    user, limited = _authenticate(request)
    if limited:
        return _json({'message': 'Too many attempts', 'code': 2001}, status=429)
    if user is None:
        return _unauthorized()
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return _json({'message': 'Invalid JSON', 'code': 2003}, status=400)
    if not isinstance(payload, dict):
        return _json({'message': 'Invalid progress', 'code': 2003}, status=400)
    document = payload.get('document')
    position = payload.get('progress')
    percentage = payload.get('percentage', 0)
    device = payload.get('device', '')
    device_id = payload.get('device_id', '')
    if (not isinstance(document, str) or not document
            or not isinstance(position, str) or not position):
        return _json({'message': 'Invalid progress', 'code': 2003}, status=400)
    if len(document) > 128:
        return _json({'message': 'Document id too long', 'code': 2003}, status=400)
    if isinstance(percentage, bool) or not isinstance(percentage, (int, float)):
        return _json({'message': 'Invalid percentage', 'code': 2003}, status=400)
    percentage = float(percentage)
    if not 0 <= percentage <= 1:
        return _json({'message': 'Invalid percentage', 'code': 2003}, status=400)
    if not isinstance(device, str):
        device = ''
    if not isinstance(device_id, str):
        device_id = ''
    metadata = payload.get('metadata')
    if metadata is not None and not isinstance(metadata, dict):
        metadata = None
    try:
        record = kosync.update_api_progress(
            user, document, position, percentage,
            device[:128], device_id[:128], metadata,
        )
    except Exception:
        logger.exception('KoSync progress update failed')
        return _json({'message': 'Server error', 'code': 2000}, status=500)
    return _json({
        'document': record.document,
        'timestamp': int(record.updated_at.timestamp()),
    })


@csrf_exempt
@require_http_methods(['GET'])
def get_progress(request, document):
    if not kosync.server_enabled():
        return _disabled()
    user, limited = _authenticate(request)
    if limited:
        return _json({'message': 'Too many attempts', 'code': 2001}, status=429)
    if user is None:
        return _unauthorized()
    record = kosync.get_progress(user, document)
    return _json(kosync.serialize_progress(record))
