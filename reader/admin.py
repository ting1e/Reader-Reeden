from django.contrib import admin
from django.forms import ModelForm, Textarea

from .models import *

class UserSettingForm(ModelForm):
    class Meta:
        model = UserSetting
        fields = '__all__'
        widgets = {
            's3_setting': Textarea(attrs={'rows': 15, 'cols': 100}),
        }

class UserSettingAdmin(admin.ModelAdmin):
    form = UserSettingForm

admin.site.register(Book)
admin.site.register(Chapter)
admin.site.register(BookGroup)
admin.site.register(UserBookRecord)
admin.site.register(UserSetting, UserSettingAdmin)
admin.site.register(UserBookMark)
