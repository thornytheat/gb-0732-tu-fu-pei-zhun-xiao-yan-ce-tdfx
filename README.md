# 图幅配准校验册

县域测绘档案员用它核对历史图幅的像素点表、地面坐标册与独立拼缝见证表。册页汇总图幅配准、源行隔离、拼缝偏差与签审结果；导出的证据包可供下一班复核。

## 启动与验证

```bash
docker compose up --build
```

打开 `http://localhost:18421/`，选「河西四幅 · 外业原件」并点击「运行校验」。图幅索引可切换到单幅，拼缝索引可展开逐站偏差。左栏各章可预检样例，也可查看已运行批次的证据。保持参数不变，点击「重跑校验」复用同一批次。工具条提供打印册页与 JSON 证据下载。

外业补测回来后，在「修订批次」章登记补测表（留空则用图册登记的 `resurvey.csv` 样例），基于当前校验批次生成修订批次：同名复测点以补测为准替换，被替换的原件行转入隔离保留回查，页面逐幅说明替换点、重算拼缝与失效签审；未变化图幅的签审随修订沿用。同一补测重跑复用同一修订批次，修订失败保留旧版，基底批次的记录与签审始终可回查。

```bash
docker compose exec -T app pytest -q
```

本地使用 Python 3.12 安装依赖并运行。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
uvicorn app.main:app --port 18421
```

Compose 将批次与签审保存到 `register-data` 命名卷。需要空册时可通过 `REGISTER_DB_PATH` 指向一个新 SQLite 文件。本地默认文件是 `data/register.sqlite3`。

## 模块和入口

| 章节 | 入口 | 校验动作 |
|---|---|---|
| 点表收件 | `GET /api/intake` | 解码、识别空档和分段表头、保留物理行范围 |
| 坐标换算 | `GET /api/coordinates` | WGS84 与本地米／厘米换成 ENU 平面米 |
| 控制点配对 | `GET /api/pairing` | 点名归并、重测冲突隔离、像素与地面对表 |
| 仿射拟合 | `GET /api/affine` | 仿射最小二乘、秩检查、逐点初始残差 |
| 离群重估 | `GET /api/refit` | 阈值内多数点拟合、稳定复估、保留离群依据 |
| 接缝验核 | `GET /api/seams` | 复用筛查、证明覆盖与次序、贴边独立测距 |
| 图幅签审 | `POST /api/review/runs` | 原件勾稽、图边签审判定、批次留存 |
| 修订批次 | `POST /api/review/runs/{run_id}/revisions` | 补测替换复测点、逐幅差异说明、签审失效与沿用 |

预检入口接收 `dataset=field` 或 `dataset=reference`，以及可选的 `control_limit_m`、`seam_limit_m`。运行接口接收同名 JSON 字段。`GET /api/review/runs/{run_id}` 回读，`POST /api/review/runs/{run_id}/signatures` 对可签图幅录名，`GET /api/review/runs/{run_id}/export` 下载一份证据。修订接口接收可选的 `filename`、`content`（补测表文本，缺省读图册登记的 `resurvey.csv`），`GET /api/review/resurvey-sample` 返回登记的样例补测表。接口目录在 `/docs`。

默认原件包含一个高影响离群点、冲突重测及一幅共线退化图。河西和青石桥均可拟合，但两图之间的见证偏差超限；柳湾可以签审。校准副本的 48 个控制点采信、三条边通过，四幅均可签。

[字段、阈值与支持范围](docs/DATA-DICTIONARY.md) · [业务测试与陷阱](docs/DEPTH.md)
