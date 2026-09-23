# Track-Pair Audit Service

硅微条探测器一列击中（4–180 个，按位置严格递增、ID 唯一）与至多 4000 条候选配对
（端点互异、不重复、ID 唯一、残差为非负整数）的非交叉匹配审计服务。

## 优化目标（严格优先级）

1. **最大化已配对击中数**（即配对数 × 2）；
2. 在其约束下 **最小化残差总和**；
3. 仍并列时，取 **按左端位置顺序的候选 ID 序列字典序最小** 的规范解。

允许嵌套（弧 (1,4) 内可含 (2,3)），禁止端点共享与几何交叉（`a<c<b<d`）。

求解采用区间 DP：`F(i,j)` 在 `i` 未配对（`F(i+1,j)`）与 `i` 与某 `k` 配对
（`1 + F(i+1,k-1) + F(k+1,j)`，天然支持嵌套）之间取最优；同时维护任意精度
（Python 大整数）的最优方案计数、规范 ID 序列，以及候选对的必选/可选/从不集合。

## API

运行时仅依赖 Python 3.11 标准库。

### `GET /healthz`

```json
{"status": "ready"}
```

### `POST /audit`

请求：

```json
{
  "hits": [
    {"id": "h1", "position": 1},
    {"id": "h2", "position": 2},
    {"id": "h3", "position": 3},
    {"id": "h4", "position": 4}
  ],
  "candidates": [
    {"id": "p1", "endpoints": ["h1", "h4"], "residual": 0},
    {"id": "p2", "endpoints": ["h2", "h3"], "residual": 0},
    {"id": "p3", "endpoints": ["h1", "h2"], "residual": 0},
    {"id": "p4", "endpoints": ["h3", "h4"], "residual": 0}
  ]
}
```

- 端点可按任意顺序给出，服务按位置归并为左/右端；位置仅用于校验严格递增。
- `id` 接受任意可区分的 JSON 值（字符串、整数等，`1` 与 `true` 视为不同）。

响应（嵌套解与相邻解同优，计数为 2，规范解取左端首位 ID 更小者）：

```json
{
  "solution_count": "2",
  "paired_hit_count": 4,
  "total_residual": 0,
  "canonical_pair_ids": ["p1", "p2"],
  "canonical_pairs": [
    {"id": "p1", "endpoints": ["h1", "h4"], "residual": 0},
    {"id": "p2", "endpoints": ["h2", "h3"], "residual": 0}
  ],
  "unpaired_hit_ids": [],
  "candidate_audit": {
    "required": [],
    "optional": ["p1", "p2", "p3", "p4"],
    "never": []
  }
}
```

- `solution_count`：达到前两级目标（最大配对数、最小残差）的方案数，
  以**任意精度十进制字符串**返回；空候选为合法请求，返回唯一的空方案 `"1"`。
- `candidate_audit` 依据全部达到前两级目标的方案分类：
  - `required`：必选（出现在每个最优方案）；
  - `optional`：可选（部分最优方案出现）；
  - `never`：从不出现。

### 错误（HTTP 422 / 400）

错误响应**只含** `error`，不夹带任何审计字段，并带字段路径：

```json
{"error": {"message": "both endpoints must reference known hit ids",
           "path": "candidates[0].endpoints"}}
```

覆盖：重复端点对（含逆序）、未知端点、端点相同、位置不严格递增、ID 重复、
残差非非负整数、击中数越界（<4 或 >180）、候选数越界（>4000）、字段缺失、
JSON 非法等。

## 运行

```bash
# 宿主机端口可配置（默认 8080）
HOST_PORT=9090 docker compose up --build api

curl http://127.0.0.1:9090/healthz
```

镜像由仓库根目录的 `Dockerfile` 构建；容器通过 `/healthz` 配置健康检查。

## 一次性复核服务 `verify`

```bash
docker compose build
docker compose run --rm verify      # 或 build 后 up，退出码即结论
# 等价: docker compose up --build verify（依赖 api 健康后执行）
```

`verify` 为单次服务（`restart: "no"`），等待 `api` 健康后执行并以退出码表示成败：

1. **构建检查**：字节编译全部模块并导入 `solver`/`server`；
2. **代码测试**：`unittest` 测试套件（27 个，含计数/规范序/分类/校验边界）；
3. **API/HTTP 冒烟**：健康检查、嵌套同优、交叉低价诱饵、空候选、非法引用
   （校验错误体无审计字段）、非法 JSON。

本地无 Docker 时可等价运行：

```bash
python -m unittest discover -s tests -v
PORT=8080 python app/server.py &
AUDIT_BASE_URL=http://127.0.0.1:8080 python scripts/verify.py
```

## 文件结构

```
app/solver.py       # 校验 + 区间 DP 求解（任意精度计数、规范解、审计分类）
app/server.py       # 标准库 HTTP 服务：GET /healthz、POST /audit
tests/test_solver.py
scripts/verify.py   # 一次性复核门（构建检查 + 单测 + HTTP 冒烟）
Dockerfile
docker-compose.yml  # api（可配置宿主端口、健康检查）+ verify（单次）
```
