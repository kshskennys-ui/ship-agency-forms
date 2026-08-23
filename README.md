# 船代业务表单系统 MVP

第一版采用 FastAPI + SQLAlchemy + SQLite，前端为轻量静态页面，方便先验证业务流程。后续可以在不改变业务数据模型的情况下替换为 PostgreSQL，并将前端迁移为 Vue 3 + TypeScript。

## 启动

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH='backend'
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

打开 http://127.0.0.1:8000 。

桌面已提供“船代系统一键启动”快捷方式，双击即可启动服务并打开浏览器。

当前实现：船舶档案、航次、`.xls/.xlsx` 船员名单导入、船员统计、换班记录、吨税申请数据保存、预报文本生成。

默认数据库是 `data/ship_agency.db`。后期切换 PostgreSQL 时，只需设置 `DATABASE_URL`，例如使用 `postgresql+psycopg://...`，业务接口不变。
