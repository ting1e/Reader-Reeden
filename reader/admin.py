from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.forms import ModelForm, Textarea

from .models import *
from .services.kosync import set_auth_hash

class UserSettingForm(ModelForm):
    class Meta:
        model = UserSetting
        fields = '__all__'
        widgets = {
            's3_setting': Textarea(attrs={'rows': 15, 'cols': 100}),
            'chapter_rule': Textarea(attrs={'rows': 6, 'cols': 100}),
        }

class UserSettingAdmin(admin.ModelAdmin):
    form = UserSettingForm

admin.site.register(Book)
admin.site.register(Chapter)
admin.site.register(BookGroup)
admin.site.register(UserBookRecord)
admin.site.register(UserSetting, UserSettingAdmin)
admin.site.register(UserBookMark)
admin.site.register(BookList)
admin.site.register(BookListItem)
admin.site.register(ReadStat)


@admin.register(SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not SystemSetting.objects.exists()


@admin.register(KosyncProgress)
class KosyncProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'document', 'percentage', 'device', 'updated_at')
    search_fields = ('user__username', 'document')


class KosyncUserAdmin(BaseUserAdmin):
    """Keep UserSetting.kosync_auth_hash in sync with admin password changes."""

    def user_change_password(self, request, id, form_url=''):
        response = super().user_change_password(request, id, form_url)
        password = request.POST.get('password1') if request.method == 'POST' else None
        if password and getattr(response, 'status_code', 0) == 302:
            user = get_user_model().objects.filter(pk=id).first()
            if user is not None:
                set_auth_hash(user, password)
        return response


User = get_user_model()
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass
admin.site.register(User, KosyncUserAdmin)
