# Reader

## 预览
![](/demo/reader.png)
## 已实现功能
- 文章阅读页面
- 设置中字体，背景更改
- 目录 书签
- 全文搜索，结果跳转
- 小说上传，删除 自动分章
- 账号功能
- 阅读记录，重新打开自动跳转倒上次阅读位置

## 未来
- 书源功能
- 爬虫功能
- 分章规则自定义
- 后台书籍管理优化

## 部署时
### 修改mysite/settings.py
+ 修改 SECRET_KEY = 'XXXX'
+ 修改 DEBUG = False
+ 修改 ALLOWED_HOSTS = ['host']
+ 修改 CSRF_TRUSTED_ORIGINS = ['host']