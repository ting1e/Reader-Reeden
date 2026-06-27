from django.core.cache import cache

LOGIN_FAIL_THRESHOLD = 5
LOGIN_FAIL_WINDOW = 300


def get_client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or ''


def _key(ip):
    return f'login_fail:{ip}'


def check_login_allowed(ip):
    fails = cache.get(_key(ip), 0)
    return fails < LOGIN_FAIL_THRESHOLD


def record_login_failure(ip):
    fails = cache.get(_key(ip), 0) + 1
    cache.set(_key(ip), fails, LOGIN_FAIL_WINDOW)


def reset_login_failures(ip):
    cache.delete(_key(ip))
