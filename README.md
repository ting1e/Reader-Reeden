# Reader-Reeden

基于 Django 的 Web 在线阅读器，支持本地/S3 书籍、自动分章、多端进度同步、翻页/滑动双模式。

**支持与 Reeden 通过 S3 双向同步阅读进度。** 个人项目，与 Reeden 无任何关系。

## 预览

| 阅读界面 | 书库列表 |
|---------|---------|
| ![](/demo/r1.png) | ![](/demo/r2.png) |

## 功能

### 书籍管理

- **本地书库** — 网格卡片，封面+书名+进度，按名称/时间/进度/最近阅读排序，实时搜索
- **远程书库** — 浏览 S3 中 `.txt` 文件，一键下载导入
- **书籍管理** — 表格管理（字数、章节、进度、共享、时间），支持重新分章、共享切换、删除
- **书单管理** — 创建书单，从书库/远程书库添加或手动录入外部书籍；书籍入库后自动关联本人书单中的同名外部条目
- **书签管理** — 跨书籍浏览全部书签，支持删除
- **书籍上传** — 拖拽上传 `.txt`，自动检测编码并分章，单文件最大 100MB
- **权限** — 私有书籍仅上传者与超管可见；管理操作仅限上传者或超管；用户数据按目录隔离

### 阅读体验

- **翻页模式** — CSS 多栏横向翻页，键盘 ←→ 翻页、↑↓ 翻章，页码省略号自适应屏宽
- **滑动模式** — 连续纵向滚动，章节自动拼接，滚至顶/底自动加载上下章
- **自动阅读** — 速度滑块 0.1~10 px/帧，手动滚动暂停后恢复，末尾自动停止
- **全文搜索** — 模态框展示命中行，点击跳转
- **章节预加载** — 预加载前后 10 章到内存，切章无刷新
- **阅读设置** — 字号、字体、颜色、粗细、字距、行距；5 种背景主题；设置服务端持久化

### 进度同步

- 滚动/翻页自动保存（防抖 500ms），关页用 `sendBeacon` 兜底
- 打开书籍时比较本地文件 / 数据库 / S3 三方时间戳，取最新进度
- 重新分章时进度按百分比自动映射
- 纯本地上传书籍仅存 DB，不同步 S3

### 阅读统计

- 今日/累计阅读时长与字数
- 近 30 天与近一年热力图
- 书籍阅读时长排行

### 用户系统

- 登录失败限流：同 IP 5 分钟 5 次后锁定
- 首次运行引导创建超级管理员
- 个人设置：云端存储配置、分章规则（主规则 + 2 条备用）、修改密码

### 字体管理

- 浏览云端字体库，一键下载
- 本地字体表格管理，阅读界面加载更换

### 界面

- DaisyUI 5 + Tailwind 4，卡片式管理页面，统一视觉风格
- 35 种主题可切换，设置实时生效

## 快速开始

```bash
pip install -r requirements.txt
cd Reader-Reeden
python manage.py migrate
python manage.py runserver
```

访问 `http://127.0.0.1:8000/`，首次自动跳转 `/setup/` 创建管理员。

## Docker 部署

镜像 `ting1e/reader-reeden` 支持多架构（amd64 + arm64）。

### 拉取并运行

```bash
docker compose up -d
```

访问 `http://localhost:8000/`，数据持久化在 `./local/`。

### docker-compose.yml

```yaml
services:
  reader:
    image: ting1e/reader-reeden:latest
    container_name: reader-reeden
    ports:
      - "8000:8000"
    volumes:
      - ./local:/app/local
    environment:
      DJANGO_DEBUG: "False"
      DJANGO_ALLOWED_HOSTS: "*"
      DJANGO_CSRF_TRUSTED_ORIGINS: ""
    restart: unless-stopped
```

### 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `DJANGO_DEBUG` | `False` | DEBUG 模式 |
| `DJANGO_ALLOWED_HOSTS` | `*` | 允许的 Host，逗号分隔 |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | 空 | CSRF 信任来源，HTTPS 域名访问时需填 |
| `DJANGO_SECRET_KEY` | 空 | 优先于 `local/secret_key.txt`；均未设置时首次运行自动生成 |

### 数据持久化

`./local/` 挂载到 `/app/local`：

```
local/
├── db.sqlite3              # 数据库
├── secret_key.txt          # SECRET_KEY
├── .device_id              # 本机设备 ID（进度同步）
├── logs/                   # 日志
└── {user_id}/              # 按用户隔离
    ├── books/              # S3 下载书籍
    ├── upload/             # 本地上传书籍
    ├── book_progress/      # 进度 JSON
    └── fonts/              # 字体文件
```

## 手动部署

通过环境变量覆盖（见上表），或修改 `mysite/settings.py`：

```python
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com']
CSRF_TRUSTED_ORIGINS = ['https://your-domain.com']
```

## S3 配置

个人设置中配置（JSON）：

```json
{
  "accessKeyId": "YOUR_ACCESS_KEY",
  "secretAccessKey": "YOUR_SECRET_KEY",
  "region": "",
  "endpoint": "https://s3.youcloud.com",
  "bucket": "YOUR_BUCKET_NAME",
  "prefix": "Reeden DIR"
}
```

- 远程书库：`{prefix}/books/`
- 字体库：`{prefix}/fonts/`
- 进度同步：`{prefix}/book_progress/{md5}.json`

## 进度文件格式

与 Reeden 兼容的 `{md5}.json`：

```json
{
  "schemaVersion": 1,
  "bookId": "文件MD5（大写）",
  "sectionIndex": 0,
  "paragraphIndex": 0,
  "elementIndex": 0,
  "readProgress": 0,
  "lastReadTime": "2024-01-01T00:00:00.000Z",
  "deviceId": "MAC地址UUID",
  "todayStats": { "date": "2024-01-01", "devices": {} }
}
```

| 字段 | 说明 |
|------|------|
| `readProgress` | 进度 0~10000（99.99% 精度） |
| `paragraphIndex` / `elementIndex` | 段落索引 / 段内偏移（CJK 计 2） |
| `lastReadTime` | ISO 8601 UTC 时间戳 |

## 目录结构

```
Reader-Reeden/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh           # 建目录 + migrate
├── local/                  # 运行时数据（结构见「数据持久化」）
├── reader/
│   ├── templates/
│   ├── static/
│   ├── models.py
│   ├── views/              # auth/books/reader/bookmark/booklist/settings/fonts/setup/stats
│   ├── services/           # book_parser/progress/s3
│   ├── middleware.py       # 首次运行检测
│   ├── ratelimit.py
│   └── utils.py
├── mysite/
└── requirements.txt
```
