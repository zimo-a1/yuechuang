<p align="center"><img src="yuechuang/yuechuang/frontend/public/brand/yuechuang-light-v5.svg" width="240" alt="悦创 YueChuang"></p>

# 悦创 YueChuang

从一句创意到短片制作工作台。支持原创故事、长篇文本改编、素材制作、镜头视频与拼接导出。

## 创作流程

- **一句话创作**：输入故事想法，选择风格、画幅和时长，创建项目。
- **IP 库与改编**：导入自己的 TXT / DOCX，选择章节范围，依次理解原著、确定改编方向、生成分集大纲与单集剧本。
- **项目工作台**：原始内容 → AI 改写 → 资产制作 → 视频制作 → 拼接导出。
- **可控生成**：人工确认关键节点，保留资产与镜头版本，支持失败重试、任务恢复和局部重生成。
- **服务设置**：按文本、图片、视频用途配置服务连接与模型，密钥仅存后端本地配置。

本仓库是本地单机 Demo，尚未实现多用户登录与权限隔离，请仅在可信本机环境运行。视频质量受模型、提示词和素材影响，仍需人工审阅。模型调用会使用你自行配置的服务账户并产生相应费用。

## 环境

Python 3.10+、Node.js 22+、FFmpeg。前端使用 React / Next.js，后端使用 FastAPI，项目与任务状态存储于本地 SQLite。

## 本地启动

在仓库根目录执行：

```bash
cd yuechuang/yuechuang/backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.yaml.example config.yaml
cp .env.example .env
python api_server.py
```

另开一个终端，在仓库根目录执行：

```bash
cd yuechuang/yuechuang/frontend
npm ci
BACKEND_API_URL=http://127.0.0.1:8000 npm run build
BACKEND_API_URL=http://127.0.0.1:8000 npm start -- --hostname 127.0.0.1 --port 3000
```

打开 <http://127.0.0.1:3000>。进入“设置 → AI 服务”配置服务地址、密钥及模型，再开始生成。也可先在后端 `config.yaml` 或 `.env` 中配置；不要将真实密钥写入示例文件或提交到仓库。更换后端地址后需以相同 `BACKEND_API_URL` 重新构建并启动前端。

首次启动项目和 IP 库为空，请导入自己的素材。仓库不提供第三方小说、采集目录、生产数据库、账户配置或历史成片。

## 开发与验证

前端开发可使用 `npm run dev`。在前端目录运行：

```bash
npm test
npm run lint
npm run build
```

在后端目录运行：

```bash
source .venv/bin/activate
python -m pip install pytest pytest-asyncio
python -m pytest -q
```

自动测试使用隔离数据与模型替身，不代表所有服务商的实时 API 可用。`tests/manual_*.py` 是开发辅助脚本；带 `--execute` 的生成脚本可能发起收费调用。

## 文件与备份

应用源码位于 `yuechuang/yuechuang/frontend` 与 `yuechuang/yuechuang/backend`。后端 `.runtime/` 保存业务状态，`code/` 保存生成素材，均不纳入版本控制。备份运行中的数据库请使用 SQLite backup API，并同时备份本地生成素材与私有配置。

可配置的生成额度限制默认关闭；可通过环境变量开启。目录来源接口仅提供示例域名校验，接入自己的作品目录时需按实际可信来源调整；TXT / DOCX 本地导入不受影响。
