# Reader-Reeden

基于 Django 的 Web 在线阅读器，支持本地书籍、S3 远程书籍、自动分章、多端进度同步、翻页/滑动双模式。

**与 Reeden 通过 S3 双向同步阅读进度。**

本项目为个人项目，与 Reeden 无任何关系。

## 预览

| 书库列表 | 阅读界面 |
|---------|---------|
| ![](/demo/Image_2026-06-27_02-48-17_005ptffu.52w.png) | ![](/demo/Image_2026-06-27_02-48-52_grli3h5t.lu5.png) |

## 功能总览

### 书籍管理

- **本地书库** — 网格卡片展示，封面 + 书名 + 进度百分比，支持按名称/时间/进度/最近阅读排序，实时搜索过滤
- **远程书库** — 浏览 S3 存储桶中的 `.txt` 文件，一键下载导入，已导入书籍直接显示进度
- **书籍管理** — 表格列出全部书籍（字数、章节数、进度、上传时间），支持重新分章和删除
- **书签管理** — 跨书籍浏览所有书签，含书名、章节、内容摘要，支持删除
- **书籍上传** — 拖拽上传 `.txt` 文件，自动检测编码（chardet）并分章，上传后留在当前页可继续上传

### 阅读体验

- **翻页模式** — CSS 分栏横向翻页，键盘方向键导航（←→ 翻页，↑↓ 翻章），页码使用省略号自适应屏宽
- **滑动模式** — 连续纵向滚动，章节自动拼接（无限滚动），隐藏滚动条，滚动至顶/底自动加载上下章
- **自动阅读** — 一键开启自动滚动，速度滑块 0.1~5 px/帧，手动滚动自动暂停 1 秒后恢复，全书末尾自动停止
- **目录侧栏** — 抽屉式章节列表，sessionStorage 缓存，自动滚动到当前章节，点击即跳转
- **书签侧栏** — 抽屉式书签列表，点击跳转精确位置，当前章节书签高亮
- **全文搜索** — 模态框展示结果，每项直接跳转到对应章节的精确位置
- **章节预加载** — 自动预加载前后章节到内存缓存，切章无刷新

### 阅读设置

- 字号 A-/A+ 实时调节
- 5 种背景主题（白/蓝/绿/黄/黑），字体颜色自动适配
- 翻页/滑动一键切换
- 自动阅读开关 + 速度滑块

### 进度同步

- 三端同步：本地 JSON 文件 + 数据库 + S3 远程，按时间戳选取最新来源
- 重新打开自动恢复上次阅读位置
- 纯本地书籍（上传添加）仅存 DB，不产生进度文件，不与 S3 互通

### 用户系统

- Django Auth 登录/登出，模态框登录
- 个人设置：字号、背景色、阅读模式（服务端持久）
- S3 配置：用户级 AccessKey/Secret/Region/Endpoint/Bucket/Prefix
- 自定义分章正则

### 章节分章

- 内置正则匹配中文小说常见章节标题（序章、楔子、正文、第X章、番外等）
- 用户可自定义分章规则
- 重新分章时阅读记录和书签按进度百分比自动映射到新章节
- 章节内容渲染时自动跳过与标题相同的首行

## 技术栈

| 项 | 技术 |
|---|------|
| 后端 | Python / Django 4 |
| 数据库 | SQLite（开发） / 可切换 PostgreSQL |
| 前端 | DaisyUI 5 + Tailwind CSS 4 (CDN) |
| 图标 | Bootstrap Icons (CDN) |
| JS | jQuery 3 + 原生 fetch / requestAnimationFrame |
| S3 | boto3（兼容 S3 协议的对象存储） |
| 编码检测 | chardet |
| 文件上传 | jQuery Huploadify |

## 快速开始

```bash
pip install django chardet boto3
cd Reader-Reeden
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

访问 `http://127.0.0.1:8000/`，创建用户后即可使用。

### 添加书籍

1. **本地上传** — 头像下拉菜单 → 上传书籍，拖拽 `.txt` 文件，书籍存入 `local/upload/`（纯本地，不同步 S3）
2. **S3 下载** — 配置 S3 后在远程书库点击下载，书籍存入 `local/books/`
3. **调试导入** — 将 `.txt` 放入 `local/books/`，访问 `/test/` 触发（调试用）

### S3 配置

在后台管理 → UserSetting 中配置（JSON 格式）：

```json
{
  "accessKeyId": "YOUR_ACCESS_KEY",
  "secretAccessKey": "YOUR_SECRET_KEY",
  "region": "us-east-1",
  "endpoint": "https://s3.us-east-1.amazonaws.com",
  "bucket": "YOUR_BUCKET_NAME",
  "prefix": "reader/"
}
```

远程书库读取 `{prefix}books/`，进度同步路径 `{prefix}book_progress/{md5}.json`。

### 进度文件格式

与 Reeden 兼容的进度 JSON（`{md5}.json`）：

```json
{
  "schemaVersion": 1,
  "bookId": "文件MD5哈希值（大写）",
  "sectionIndex": 0,
  "paragraphIndex": 0,
  "elementIndex": 0,
  "readProgress": 0,
  "lastReadTime": "2024-01-01T00:00:00.000Z",
  "deviceId": "服务器MAC地址UUID",
  "todayStats": {
    "date": "2024-01-01",
    "devices": {
      "<deviceId>": {
        "readSeconds": 0,
        "wordCount": 0,
        "hourly": {}
      }
    }
  },
  "chapterId": 1,
  "wordsRead": 0
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `bookId` | string | 书籍文件的 MD5 哈希（大写），作为唯一标识 |
| `sectionIndex` | int | 当前章节在章节列表中的 index |
| `paragraphIndex` | int | 当前段落（行）索引 |
| `elementIndex` | int | 段内字符偏移（CJK 字符计 2） |
| `readProgress` | int | 阅读进度 0~10000（99.99% 精确度） |
| `lastReadTime` | string | ISO 8601 UTC 时间戳 |
| `wordsRead` | int | 章节内已读字符偏移量 |

### 自定义分章规则

默认规则：

```
^(?:序章|楔子|正文|终章|后记|尾声|番外|第[\d]+[章节折卷集部篇])
```

可在阅读设置中自定义，下次分章或重新分章时生效。

## 部署

修改 `mysite/settings.py`：

```python
SECRET_KEY = 'your-secret-key'
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com']
CSRF_TRUSTED_ORIGINS = ['https://your-domain.com']
```

## 目录结构

```
Reader-Reeden/
├── local/
│   ├── books/           # S3 下载的 .txt 书籍
│   ├── upload/          # 本地上传的 .txt 书籍（纯本地）
│   └── book_progress/   # 阅读进度 JSON
├── reader/
│   ├── templates/       # Django 模板
│   ├── static/          # CSS / JS / 图片
│   ├── models.py        # 数据模型
│   ├── views.py         # 视图
│   ├── urls.py          # 路由
│   ├── form_book.py     # 分章逻辑
│   └── migrations/      # 数据库迁移
├── mysite/              # 项目配置
├── demo/                # 截图
└── db.sqlite3
```
