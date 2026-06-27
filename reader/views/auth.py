from django.http import HttpResponse
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_protect

from ..utils import get_or_create_user_setting
from ..ratelimit import (
    check_login_allowed, record_login_failure, reset_login_failures, get_client_ip,
)


@csrf_protect
def login_auth(request):
    if request.method != 'POST':
        return HttpResponse('请求方式不正确', status=405)

    ip = get_client_ip(request)
    if not check_login_allowed(ip):
        return HttpResponse('登录失败次数过多，请稍后再试')

    username = request.POST.get('username')
    password = request.POST.get('password')
    if not username or not password:
        record_login_failure(ip)
        return HttpResponse('请输入正确的用户名或密码')

    user = authenticate(username=username, password=password)
    if user is None:
        record_login_failure(ip)
        return HttpResponse('请输入正确的用户名或密码')

    reset_login_failures(ip)
    get_or_create_user_setting(user)
    login(request, user)
    return HttpResponse('success')


def logout_view(request):
    if request.method != 'POST':
        return redirect('reader:book_list')
    logout(request)
    return redirect('reader:book_list')
