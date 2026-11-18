# 栖色织研 · 织物染样配方降维审查

36 波段光谱降维、CIELAB 色差确认与复染配方冻结，面向栖色织研 24 人染整研发组。

上游为光谱仪导出的 `reflectance_20261117.csv`，调色师现在用纸样册和 Excel 比对；36 波段难比较、浓度尺度影响距离、超色差配方仍流向车间。系统接管配方—实测染样—复染签批环节，下游车间只接收主管放行配方。

## 启动

```sh
cp .env.example .env
docker compose up -d --build
# 启动自动 seed；也可人工重复执行（只插入缺少的自然键）
docker compose exec -T backend python -m scripts.seed_demo
```

前端 [localhost:18220](http://localhost:18220)，后端 [localhost:19220/docs](http://localhost:19220/docs)，数据库 17720。停止并删除演示数据：`docker compose down -v --remove-orphans`。

|账号|角色|权限|
|---|---|---|
|admin@qise.test|admin 主管|全部录入、计算、确认、最终签批|
|colorist@qise.test|colorist 调色师|配方/染样录入、PCA、试验与色差确认，不能最终批准|
|viewer@qise.test|viewer 观察员|十页与审计记录只读|

演示密码均为 `Dye2026!`。JWT 有效期 8 小时；所有变更通过后端鉴权。数据库密码与 JWT 默认密钥为本地演示配置。

## 业务模块

- 配方目录：三种染料浓度、唯一配方编号与冻结保护；冻结不能改浓度或追加染样。
- 染样登记：配方外键、唯一批号、36 波段 0–1 反射率、CIELAB 实测值。
- PCA 图谱与配对：Redis 队列交给 Python worker，返回 60 个实算坐标、解释方差与重构误差；建立同配方染样复染配对。
- 复染签批：草稿 → 调色师确认 → 主管批准；批准自动冻结关联配方。ΔE>3 的草稿确认后进入退回修色状态，不能批准。

`ΔE76 = sqrt((L1−L2)^2+(a1−a2)^2+(b1−b2)^2)`；门限 3 为模拟工艺规则，PCA 距离不能代替色差判定。PCA 对 36 维反射率用 StandardScaler，二维 PCA / full SVD / random_state=42；重构 MSE 在标准化空间计算。小于三张染样时计算失败。seed `random.Random(42)`，60 张样本、3 个配方。Redis 结果保存 24 小时，共享 artifacts 卷留存 JSON。

## 一天剧本

1. 08:30 调色师登录，检查 DY-021 靛蓝亚麻（A1.8%、B0.4%、C0.2%）。
2. 09:00 核对 `reflectance_20261117.csv` 与 REF-021（L48/a−2/b−25）。
3. 09:30 登记 SAMPLE-021（L48.7/a−1.5/b−24.5，ΔE≈0.995）。
4. 10:00 生成 60 张染样 PCA 图谱，检查解释方差与重构误差。
5. 10:30 建立 REF-021→SAMPLE-021 复染配对。
6. 11:00 以 SAMPLE-OFF（L62/a8/b−14）测试超限，确认后退回修色。
7. 14:00 调色师确认合格试验，主管批准，配方冻结。
8. 16:00 车间观察员查签批簿与审计账号，依据冻结配方安排复染。

## 工程与本地开发

Python 3.12 / FastAPI / SQLAlchemy / PostgreSQL16 / Redis7 / scikit-learn1.6；前端原生 ES modules / HTML / CSS / Nginx1.27。

`backend/src/models.py` 定义领域模型；`schemas.py` 校验输入；`recipe_service.py` 承担领域规则和审计；`app.py` 路由及两道中间件；`atlas.py` 纯 Python ML；`worker.py` 消费队列、记录心跳；`backend/scripts/seed_demo.py` 幂等数据。首次启动 SQLAlchemy metadata 建表，本小型项目不含后续在线迁移工具。

前端 `src/api.js` API、`store.js` 状态、`utils.js` 格式与反馈、`components/common.js` 标题/状态/光谱/证据表、`pages/` 十个业务页面。角色与试验状态枚举分别位于 `backend/src/constants.py` 与 `frontend/src/constants.js`。

本地后端：创建 Python3.12 venv，安装 `backend/requirements.txt`，设置 DATABASE_URL/REDIS_URL 指向映射端口，进入 backend 运行 `uvicorn src.app:app --reload`；worker 同目录运行 `python -m src.worker`（本地 `/artifacts` 路径需有写权限）。本地前端可经现成 Nginx 容器开发，修改后 compose build frontend。

环境变量：COMPOSE_PROJECT_NAME 容器名前缀、FRONTEND_PORT/BACKEND_PORT/DB_PORT 宿主端口、JWT_SECRET 签名密钥、SEED_DEMO 自动 seed；NETWORK_SUBNET 可调整显式私有子网（默认10.221.21.0/24，规避本机默认地址池耗尽）。服务内部 DATABASE_URL、REDIS_URL 由 Compose 指定。五服务均有真实健康检查；db/redis/artifacts 三个命名卷。业务与浏览器验收在源码外 `test-artifacts/gb-1021/`。

## License

MIT，见 LICENSE。


## 业务扩展（2026-11-17）

新增两个独立业务模块，业务数据持久化到 PostgreSQL：

- **配方修订谱系**：冻结父配方复制成新子版本，保存父编号、名称与三染料浓度快照；修订理由必填。主管审核来源前，子版不能改浓度、追加染样或建立复染试验。审核后可独立调整子版，原版继续冻结。子版必须是新编号，拒绝重复子版本与循环关联。
- **生产缸次色差验收**：已批准复染试验才能下生产缸次，保存目标色样快照。登记生产 Lab 后由 Python 实算 ΔE76；超过3进入扣留，可修色后复测；合格后主管签放，放行后实测值冻结。观察员只读，调色师可登记但不能审核来源或签放生产。

新实体为 `RecipeRevision`（父子配方外键、来源快照、审核状态）与 `DyeBatch`（唯一缸号、批准试验外键、重量、Lab、色差、状态、签放人）。谱系为 `pending→approved`，生产为 `planned→hold/qualified→released`。生产扣留可重新测量；来源审核与生产签放均不重复执行。

新增接口：`GET/POST /revisions`、`POST /revisions/{id}/review`、`GET/POST /batches`、`PUT /batches/{id}/measurement`、`POST /batches/{id}/release`。五个后端业务文件位于 `backend/src/production_models.py`、`production_schemas.py`、`revision_service.py`、`batch_service.py`、`production_routes.py`；前端新页为 `pages/revisions.js` 和 `pages/batches.js`。

扩展 seed 新增 DY-PROD 冻结量产基准、PROD-REF/PROD-OK 两张实测染样、已批准量产试验和 DB-DEMO-001 待测缸次；全新库基础数据为3用户、4配方、62染样。连续seed只补缺项，保留人工编辑、修订谱系及缸次结果。本次没有更改旧表字段，自动建表为既有演示库补充两个新表；生产环境仍建议使用版本化迁移。

演示续篇：冻结原版→建立细支亚麻修订→主管审来源→调色师调整子版；从批准小样建立400kg生产缸→登记超色差实测并扣留→修色复测合格→主管签放。新的真实验收、4张1440×900截图与已知差异见 `test-artifacts/gb-1021/expansion/`。


## V3：染料称料、客户发样与色差补染

保留原配方、染样、PCA、复染签批、修订谱系和生产验收六页，新加三页，后续再加隔离换料一页，合计十个业务页面。

- **染料库存称料**：`DyeLot`按A/B/C组分记录唯一批号、可用kg、有效期和隔离状态。`DyeIssue`关联生产缸与实际来源批次，每缸每组分唯一，保留配方浓度快照和称料人。服务器计算“浓度%×生产kg÷100”，先锁缸次、按ID排序锁三个批次，全部校验后整单扣减。过期、隔离、不足或组分不匹配均不扣任一项；重复及并发领料只能成功一次。新生产签放必须A/B/C凭据齐全；历史已签放不追加领料或补扣。
- **客户色样签收**：`ColorDispatch`每个已签放缸只能发样一次，保存运单、客户、实测Lab、原目标、配方、生产kg和签放人快照。发样→实际收件人签收→接受或色差争议；未签收、跨缸结论和重复结论被拒绝。客户原争议记录冻结，后续补染另单处理。
- **色差客诉补染**：`ShadeComplaint`仅关联已签收且争议的发样单，申请kg不得超过原缸。主管裁定补染或驳回；补染须新的、未用于其他生产缸的approved trial，且新目标色与原客户目标ΔE≤3，系统按申请kg创建唯一关联新缸。新缸正常称料、实测、主管签放后，主管才可关闭；原缸、其他缸、未放行新缸或重复关闭都拒绝。

角色：调色师可入库、称料、发样、记录客户签收/反馈、提出客诉和建立已批准补染新缸；主管另外负责隔离/解除染料、补染裁定、生产签放及关闭客诉；观察员十页只读。客户反馈由内部岗位依据实际交接记录，不新增外部客户登录或对外消息。

接口：`GET/POST /dye-lots`、`PUT /dye-lots/{id}/state`、`GET/POST /dye-issues`；`GET/POST /color-dispatches`及`POST /{id}/receive`、`POST /{id}/conclusion`；`GET/POST /shade-complaints`及`POST /{id}/decision`、`POST /{id}/remake`、`POST /{id}/close`。主管专属动作由JWT后端依赖保护，非仅前端禁用。

新增代码按职责分为`fulfillment_models.py`、`fulfillment_schemas.py`、`fulfillment_routes.py`与`stock_service.py`、`dispatch_service.py`、`complaint_service.py`；前端新增`pages/stock.js`、`pages/dispatches.js`、`pages/complaints.js`。原batch_service在新建时保存配方快照、签放时核称料，补染新缸与客诉关联一次事务提交。四个新表自动补建，没有旧表列变更，保留既有released状态及旧格式目标快照。

Seed再补5个染料批次：A80kg、B40kg、C20kg可用，C隔离5kg、B已过期5kg作为拒领剧本；重复seed不补满消耗、不清除客诉、不覆盖人工修改。基础用户3、配方4、染样62仍保持；新签收与客诉从实际生产动作产生。

续篇剧本：为200kg靛蓝缸称A3.6/B0.8/C0.4kg→色差实测合格并签放→禾里服装收样，林工签收后记录标准光源色差争议→申请100kg补染→主管批准→新试验关联新缸，称A1.6/B0.4/C0.2kg→新缸正常实测签放→主管关闭客诉。新库存、客户与补染证据独立归档`test-artifacts/gb-1021/v3/`。

限制：库存仅染料批次扣料，不包含采购、货位、退库与盘点；客户运单和签收为本地人工凭据，未调用物流或客户门户。补染不出款、不改旧发样结论，不含合同索赔金额。Lab、光谱和工艺门限仍为模拟业务，需真实仪器及客户容差规范校准。


## V4：染料批次隔离换料与整缸检验

新增第十页“隔离换料与检验”。此前隔离只阻止继续领用：已整单称料但尚未实测的缸仍被隔离旧料占用，且没有依据判断换到哪一批。本版用历史称料与实测 Lab 学习批次偏色，为这些缸推荐库存可用的同组分替代批次。

- **批次偏色学习**：对每个已实测且 A/B/C 整单齐全的缸，取 `实测 Lab − 目标 Lab` 作为响应，解释变量是三个实际领用批次；另设一个不惩罚的整体工艺偏移，批次系数为相对偏色。用岭回归（`RIDGE=0.5`）分离同组分内批次并给出估计标准误。每个批次至少 `MIN_EVIDENCE=3` 个已实测缸次才参与推荐；旧批或替代批任一证据不足即不给建议。实现见 `substitution_service.py`，纯 Python/NumPy，不走 Redis。
- **推荐口径**：只对“未实测、未签放、且某一组分旧批次仍在隔离”的缸生成建议。替代批必须是同组分、状态 active、未过期、库存 ≥ 需扣量，且“换料后组合”的预测 ΔE ≤ 3。不合格候选项仍列出并写明原因，便于核对；全部不合格则不出现采纳表单。
- **整缸检验留量**：按 `浓度% × 缸重 ÷ 100` 计算用量，再按 `INSPECTION_RATE=5%` 留出整缸检验量，需扣 `用量 + 检验`。检验量随替代料一并扣出，不会二次领用。
- **主管采纳**：新增 `POST /dye-substitutions`，仅 admin。请求必须带客户端生成的 `request_id` 与逐组分替代批次。服务端在锁内重核：旧料是否仍隔离、缸是否已实测/签放、替代料状态/有效期/证据/余量。全部通过后单事务内把旧料按原领用 kg 退回原批次，从替代批扣 `用量+检验`，同步把称料凭据改为新批次并记录检验量；任一环节失败整单不变。隔离批次即使退回也仍是隔离，不可领用。
- **并发与幂等**：先锁缸次，再按批次 ID 升序锁旧批与替代批（与整单称料同序），跨缸并发不会超领。`request_id` 与 `batch_id` 均唯一：同一请求重试返回原凭据（`idempotent=true`）且不重复退扣；不同请求撞同一缸、或不同缸撞同一 `request_id`，唯一约束回滚整单并返回 409。
- **已实测与签放**：缸次一旦登记实测即拒绝换料，签放仍只依据实测 ΔE 与既有称料齐全校验；换料预测不写入实测字段，不替代签放判定。
- **页面证据**：`GET /dye-substitutions/overview` 返回门限、学习样本量、各批次偏色与证据缸数、待换料缸的建议与不合格原因、被拒绝缸次原因、换料凭据（旧批/新批、用料前后余额、退回/扣减/检验、学习偏色、预测 ΔE 与检验误差）。缸次后续实测后，凭据同时显示实测 ΔE 与 `|预测−实测|` 的检验误差。`GET /dye-substitutions/rejections` 返回主管采纳被拒的审计记录。

新增表 `dye_substitutions`（缸次唯一、request_id 唯一、合计用量/退旧/扣新/检验、整缸预测 Lab 与 ΔE、采纳人、证据快照）与 `dye_substitution_items`（组分唯一、新旧批次外键、旧批占用、用量、检验、需扣、双方余额快照、学习偏色、预测 Lab/ΔE、误差、证据）。首次启动自动补建，不改旧表。新增代码为 `substitution_models.py`、`substitution_schemas.py`、`substitution_routes.py`、`substitution_service.py` 与前端 `pages/substitutions.js`。

Seed 追加换料剧本：8 个新染料批次（含隔离、稀疏证据、超色差、余量不足样本）、16 个已实测历史缸（按批次相对偏色生成）与 6 个待处理缸次（`DB-HOLD-001`…`004`、已实测扣留的 `DB-HOLD-DONE`、证据不足的 `DB-HOLD-NOEV`）。历史与待处理缸的领用同时从批次余额扣减，重复 seed 只补缺项、不再二次扣减，也不覆盖人工采纳结果。基础用户 3、配方 4、染样 62 保持不变。

续篇剧本：主管在第十页查看学习偏色与推荐 → 对 `DB-HOLD-001` 选 A、C 替代批并整缸采纳 → 凭据显示退回旧批、扣新含检验 → 调色师实测签放 → 凭据显示实测 ΔE 与检验误差；`DB-HOLD-DONE` 因已实测被拒，`DB-HOLD-NOEV` 因证据不足不给建议。

限制：批次偏色模型为加性、线性、小样本；不改动旧称料记录的历史金额或采购，不含退库单与盘点；同一缸只允许一份换料凭据，换料后若另一组分随后被隔离，需另行人工处理，不会二次自动换料；推荐不替代实测和签放，检验留量比例仍为模拟工艺规则。
