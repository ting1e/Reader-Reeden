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
- **书签管理** — 跨书籍浏览全部书签，支持删除
- **书籍上传** — 拖拽上传 `.txt`，自动检测编码并分章，单文件最大 100MB
- **权限** — 私有书籍仅上传者与超管可见；管理操作仅限上传者或超管

### 阅读体验

- **翻页模式** — CSS 多栏横向翻页，键盘 ←→ 翻页、↑↓ 翻章，页码省略号自适应屏宽
- **滑动模式** — 连续纵向滚动，章节自动拼接，滚至顶/底自动加载上下章
- **自动阅读** — 速度滑块 0.1~5 px/帧，手动滚动暂停 1 秒后恢复，末尾自动停止
- **目录侧栏** — sessionStorage 缓存，自动滚动到当前章节
- **书签侧栏** — 点击跳转精确位置，当前章节高亮
- **全文搜索** — 模态框展示命中行，点击跳转
- **章节预加载** — 预加载前后 10 章到内存，切章无刷新

### 阅读设置

- 字号、字体、颜色、粗细、字距、行距可调
- 5 种背景主题（白/蓝/绿/黄/黑），颜色自动适配
- 翻页/滑动一键切换，自动阅读开关
- 全部设置服务端持久化

### 进度同步

- 滚动/翻页自动保存（防抖 500ms），关页用 `sendBeacon` 兜底
- 重新分章时进度按百分比自动映射
- 纯本地上传书籍仅存 DB，不同步 S3

### 用户系统

- 登录失败限流：同 IP 5 分钟 5 次后锁定
- 个人设置：S3 配置、分章规则、修改密码

### 字体管理

- 浏览 S3 字体库（`{prefix}fonts/`），一键下载
- 本地字体表格管理，阅读界面通过 `@font-face` 加载（ttf/otf/woff/woff2）



## 快速开始

```bash
pip install django chardet boto3
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


### 数据持久化

`./local/` 挂载到 `/app/local`：

```
local/
├── db.sqlite3         # 数据库
├── secret_key.txt     # SECRET_KEY
├── books/             # S3 下载书籍
├── upload/            # 本地上传书籍
├── book_progress/     # 进度 JSON
├── fonts/             # 字体文件
└── logs/              # 日志
```


## 手动部署

修改 `mysite/settings.py` 或通过环境变量覆盖：

```python
DEBUG = False
ALLOWED_HOSTS = ['your-domain.com']
CSRF_TRUSTED_ORIGINS = ['https://your-domain.com']
```

`SECRET_KEY` 优先读环境变量 `DJANGO_SECRET_KEY`，其次 `local/secret_key.txt`，首次运行自动生成。

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
  "todayStats": { "date": "2024-01-01", "devices": {} },
  "chapterId": 1,
  "wordsRead": 0
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
├── Dockerfile              # 镜像定义
├── docker-compose.yml      # Compose 编排
├── entrypoint.sh           # 入口脚本（建目录 + migrate）
├── local/                  # 运行时数据（挂载卷）
│   ├── db.sqlite3
│   ├── secret_key.txt
│   ├── books/              # S3 下载书籍
│   ├── upload/             # 本地上传书籍
│   ├── book_progress/      # 进度 JSON
│   ├── fonts/              # 字体
│   └── logs/
├── reader/
│   ├── templates/          # 模板
│   ├── static/             # CSS/JS/图片
│   ├── models.py           # 数据模型
│   ├── views/              # 视图（auth/books/reader/bookmark/settings/fonts/setup）
│   ├── services/           # 业务逻辑（book_parser/progress/s3）
│   ├── middleware.py       # 首次运行检测
│   ├── ratelimit.py        # 登录限流
│   └── utils.py
├── mysite/                 # 项目配置
└── requirements.txt
```
