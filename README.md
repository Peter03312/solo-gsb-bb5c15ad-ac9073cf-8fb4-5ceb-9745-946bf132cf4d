# 共享厨房过敏原交叉污染排程 · Shared Kitchen Allergen Scheduling

一个基于 **Python + SQLite** 的 Web 应用：管理员录入待制作批次的过敏原集合、使用的台面/器具、
预计时段与清洁记录后，系统按照 **器具占用** 与 **清洁有效期** 自动生成可执行顺序，
并为每个批次标出风险来源。

## 核心规则

1. **器具占用**：同一台面/器具在同一时段只能安排一个批次，时段重叠即阻塞并保留原因。
2. **切换过敏原前的清洁要求**：某批次要在器具上使用含过敏原 A 的原料时，
   该器具上最近一次沉积 A 残留（上一个已排入的含 A 批次）之后，必须存在一条：
   - 覆盖过敏原 A（明确勾选，或“覆盖全部过敏原”）；
   - 完成时间晚于残留沉积、早于本批次开始；
   - 且在本批次计划开始时刻**仍在有效期内**的清洁记录。
3. **拒绝排入**：缺少覆盖全部残留源的清洁、清洁已失效、或时段冲突时，
   批次被标记为「阻塞」，阻塞原因逐条保留；被阻塞批次不沉积残留、不占用器具。
4. **风险来源标注**：已排入批次的每条受控残留风险都会标明
   来源批次、器具、过敏原、所依据的清洁记录及其有效期。
5. **人工调整顺序**：可上下调整批次优先级后重新计算（阻塞批次会被跳过，影响后续判断）。
6. **导出**：一键导出排程与风险说明（CSV / Markdown）。

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

启动后打开页面顶部显示的访问地址（默认）：

**http://localhost:8000**

> 首次打开可在「排程」页点击「载入演示数据」，快速体验冲突、缺清洁、清洁过期三类典型场景。

## 配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8000` | 容器监听端口 |
| `PUBLIC_URL` | `http://localhost:8000` | 页面上显示的对外访问地址（反代/域名部署时修改） |
| `DB_PATH` | `/data/kitchen.db`（容器内） | SQLite 数据库文件路径，已挂载为卷持久化 |

修改端口示例：

```bash
PORT=8080 docker compose up --build
# 同时把 docker-compose.yml 的 ports 改为 "8080:8080"，PUBLIC_URL 改为 http://localhost:8080
```

数据持久化目录：`./data`（挂载到容器 `/data`）。

## 本地开发运行（无需 Docker）

应用本身只依赖 Python 3.9+ 标准库：

```bash
python3 app.py
# * Running on http://localhost:8000
```

数据库文件默认在 `./kitchen.db`，可用 `DB_PATH` 覆盖。
生产容器使用 gunicorn 多线程承载同一套 WSGI 应用（见 `requirements.txt`）。

## 页面与 API 说明

- 页面标签页：**批次 / 清洁记录 / 台面与器具 / 排程**
- 排程页：调整顺序 →「重新计算」→ 查看每个批次的阻塞原因与受控风险 → 导出 CSV / Markdown

主要接口（均返回 JSON）：

| 方法 | 路径 | 功能 |
| --- | --- | --- |
| GET | `/api/meta` | 访问地址、端口等元信息 |
| GET/POST | `/api/allergens` | 过敏原列表 / 新建自定义过敏原 |
| GET/POST | `/api/equipment` | 台面器具列表 / 新建 |
| GET/POST/DELETE | `/api/batches[/{id}]` | 批次 CRUD（过敏原集合 + 器具集合 + 时段） |
| GET/POST/DELETE | `/api/cleanings[/{id}]` | 清洁记录（覆盖过敏原、有效期） |
| POST | `/api/schedule/run` | 按给定顺序计算排程（body：`{"order": [批次ID...]}`，可省略） |
| GET | `/api/schedule/latest` | 最近一次排程结果 |
| GET | `/api/schedule/export?format=csv\|md` | 导出排程与风险说明 |
| POST | `/api/admin/seed-demo` | 载入/重置为演示数据 |

## 目录结构

```
app.py            # WSGI 路由与 REST API（标准库实现）
scheduler.py      # 占用 / 清洁有效期 / 残留风险排程算法
db.py             # SQLite 连接、建表、内置过敏原
static/           # 前端页面（原生 HTML/CSS/JS，无 CDN 依赖）
Dockerfile        # gunicorn 承载
docker-compose.yml# 一键启动 + 卷持久化 + 端口/地址配置
requirements.txt  # 仅 gunicorn（算法与服务代码零三方依赖）
```
