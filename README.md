# Reader-Reeden

基于 Django 的 Web 在线阅读器，支持本地/S3 书籍、自动分章、多端进度同步。

**支持与 Reeden 通过 S3 双向同步阅读进度。** 个人项目，与 Reeden 无任何关系。

## 预览

| 阅读 | 统计 |
|---------|---------|
| ![](/demo/r1.png) | ![](/demo/r4.png) |

| 书库 | 书单 |
|---------|---------|
| ![](/demo/r2.png) | ![](/demo/r5.png) |


## 功能

### 书籍管理

- **本地书库** — 网格卡片，封面+书名+进度，按名称/时间/进度/最近阅读排序，实时搜索
- **远程书库** — 浏览 S3 中 `.txt` / `.epub` 文件，一键下载导入
- **书籍管理** — 表格管理（字数、章节、进度、共享、时间），支持重新分章、共享切换、删除
- **书单管理** — 创建书单，从书库/远程书库添加或手动录入外部书籍；书籍入库后自动关联本人书单中的同名外部条目
- **书签管理** — 跨书籍浏览全部书签，支持删除
- **书籍上传** — 拖拽上传 `.txt` / `.epub`，单文件最大 100MB；TXT 自动检测编码分章，EPUB 提取书名作者并按原书目录阅读
- **权限** — 私有书籍仅上传者与超管可见；管理操作仅限上传者或超管；用户数据按目录隔离

### 阅读体验

- **翻页模式** — CSS 多栏横向翻页，键盘 ←→ 翻页、↑↓ 翻章，页码省略号自适应屏宽
- **滑动模式** — 连续纵向滚动，章节自动拼接，滚至顶/底自动加载上下章
- **自动阅读** — 速度滑块 0.1~10 px/帧，手动滚动暂停后恢复，末尾自动停止
- **全文搜索** — 模态框展示命中行，点击跳转
- **章节预加载** — 预加载前后 10 章到内存，切章无刷新
- **阅读设置** — 字号、字体、颜色、粗细、字距、行距；5 种背景主题；设置服务端持久化

### 进度同步

- 滚动/翻页自动保存（防抖 500ms）
- 打开书籍时比较本地文件 / 数据库 / S3 三方时间戳，取最新进度
- 重新分章时进度按百分比自动映射
- 纯本地上传书籍仅存 DB，不同步 S3
- EPUB 保留原文件 MD5、原始阅读顺序及段落位置映射，支持 S3 进度恢复/保存；打开书籍本身不回写云端进度

### EPUB 支持范围

- 支持无 DRM 的 EPUB 2/3，默认“原书排版”阅读；保留“纯文本”入口。TXT 阅读界面不变。
- EPUB 原书排版/纯文本共用 TXT 阅读页面的顶部导航、底部分页、目录/书签侧栏和阅读设置。在设置的“正文显示”中切换，每本书记住上次选择；切换按正文位置衔接，不单独回写云端进度。无文字的图片书禁用纯文本选项。
- 原书排版界面支持正文图片与点击放大、表头/合并单元格及宽表滚动、书内 CSS/未加密字体、原书/自定义样式切换；书签沿用阅读器整体入口，与纯文本共用同一套记录。
- 章节先按当前样式预渲染再进入 iframe，首帧即为最终样式，不会先显示原书样式再切换；切换“原书样式/自定义样式”会按正文位置重新加载当前章节，其余设置即时生效。
- 支持章节及跨章锚点跳转、脚注弹窗与返回、全文搜索、书签；英文标点按原文保留，不转换为中文标点。
- 固定版式按原页面尺寸显示，支持适应页面、100%/150%/200% 缩放、翻页和页面进度；暂不实现双页跨页拼接。
- 书内脚本、表单、嵌入网页、音视频和外部资源禁用；原书 CSS 经安全过滤，并非任意 CSS/脚本的完整兼容。
- 原 EPUB 与生成的 `.reader.json` 正文缓存一同保存在用户目录，备份 `local/` 时需一起保留；删除书籍会同时清理两者。
- EPUB 使用原书目录，不提供 TXT 正则重新分章；目录显示编号与同步用的原始阅读顺序索引分开。
- 云端 JSON 沿用 `sectionIndex` / `paragraphIndex` / `elementIndex`，另保留本阅读器精确定位扩展；`readProgress` 按样本采用阅读条目索引占比，本页面则显示包含章内位置的进度。索引无效时按进度近似恢复，不在打开时回写。
- 跨 Reeden 的章节顺序及 JSON 结构已按实际样本适配；不同阅读器对图片、空白、标题的段落划分仍可能存在差异，首次使用建议核对落点。
- 原书排版位置另保存页面与正文锚点；图片/字体加载完成后再分页。固定版式、图片页的精确页面位置使用扩展字段，本地书签同样保留页面位置。
- ZIP 限制：原文件 100MB、解压后总大小 200MB、单资源 16MB、最多 5000 个条目；不解压资源到磁盘，不访问书内远程链接。

### 阅读统计

- 今日/累计阅读时长与字数
- 近 30 天与近一年热力图
- 书籍阅读时长排行

### 用户系统

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
