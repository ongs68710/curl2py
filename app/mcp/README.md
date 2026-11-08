# curl2py MCP server

把 curl2py 的 21 个工具暴露成 MCP 工具（可选开到 23 个），支持 **stdio** 和 **Streamable HTTP** 两种传输，
零第三方依赖（设备与本机都无外网，装不了官方 `mcp` SDK，协议用标准库手写）。

> 给使用者看的版本在 [`使用指南.md`](./使用指南.md)——大白话、怎么接、怎么用、出错怎么办。
> 本文是给改代码和排障的人看的。

```
stdio:  MCP 客户端 ──stdin/stdout──► server.py ──HTTP──► curl2py:5000
http :  局域网客户端 ──POST /mcp──► server.py ──HTTP──► curl2py:5000
```

## 三种部署形态

| 形态 | 传输 | 适用 | 端口 |
|---|---|---|---|
| 宿主机脚本 | stdio | 单机 agent（WorkBuddy / Cursor 本地） | 无 |
| 宿主机进程 | http | 局域网内其他设备连你的 PC | 9999 |
| **容器内嵌** | http | **玩客云/群晖这类常开设备，客户端关机也能用** | 9999 |

容器内嵌是推荐形态：MCP 与工具箱同机同镜像，设备一开机就可用，不依赖任何一台 PC 开机。

## 启动

```bash
# stdio（客户端替你拉起，不要手动跑）
python mcp/server.py --base-url http://127.0.0.1:9997

# HTTP（宿主机自测）
python mcp/server.py --transport http --host 127.0.0.1 --port 9999 \
    --base-url http://127.0.0.1:9997

# HTTP（容器内，必须监听 0.0.0.0）
python /app/mcp/server.py --transport http --host 0.0.0.0 --port 9999 \
    --base-url http://192.168.0.107:9997
```

## 参数

| 参数 | 环境变量 | 默认 | 说明 |
|---|---|---|---|
| `--base-url` | `CURL2PY_BASE_URL` | `http://127.0.0.1:9997` | 工具箱后端地址 |
| `--token` | `CURL2PY_API_TOKEN` | 空 | 后端开启鉴权时的 Token（`Bearer`） |
| `--timeout` | — | 30 | 单次转发超时秒数 |
| `--transport` | — | `stdio` | `stdio` 或 `http` |
| `--host` | `CURL2PY_MCP_HOST` | `127.0.0.1` | HTTP 监听地址 |
| `--port` | `CURL2PY_MCP_PORT` | 9999 | HTTP 监听端口 |
| `--http-token` | `CURL2PY_MCP_TOKEN` | 空 | 非空则要求 `Authorization: Bearer <token>`（`/health` 不拦） |
| `--allow-origin` | — | 无 | 允许的浏览器 Origin，可重复。默认拒绝所有带 `Origin` 的请求 |
| `--max-concurrency` | `CURL2PY_MCP_MAX_CONCURRENCY` | 64 | 同时处理的连接数上限，超出直接回 `503`。单核设备降到 32 也够用 |
| `--allow-script-run` | — | 关 | 额外注册 `run_python`（需后端 `ENABLE_SCRIPT_RUNNER=true`）。**HTTP 模式下不给 `--http-token` 会拒绝启动** |
| `--allow-local-file` | — | 关 | 额外注册 `file_to_base64`（可读服务端本地文件） |
| `--local-file-root` | `CURL2PY_MCP_FILE_ROOT` | 空 | 限制 `file_to_base64` 只能读该目录下的文件，`realpath` 校验，软链接也绕不过去 |
| `--list-tools` | — | — | 打印工具清单后退出 |

## HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/mcp` | 收发 JSON-RPC，单条或批量数组；只有通知时返回 `202` |
| GET | `/mcp` | `405`（本服务端不主动推送，规范允许） |
| DELETE | `/mcp` | 结束会话，返回 `200` |
| GET | `/health` | 服务名、版本、工具数、运行时长、调用/错误计数、按工具耗时、RSS（容器探针用） |

会话：`initialize` 响应头下发 `Mcp-Session-Id`；客户端自带未知 id 时服务端顺手登记，
不返回 404（部分客户端会自己生成 GUID，强校验会导致握手失败）。

`/health` 的字段够定位大多数现场问题：

| 字段 | 含义 |
|---|---|
| `calls` / `errors` | JSON-RPC 层的工具调用数与其中失败数 |
| `http_requests` / `http_rejected` | 传输层请求数 / 被拒数（含 503 背压） |
| `by_tool` | 每个工具的 `ok` / `err` / `avg_ms` |
| `slowest_tool_ms` | 进程启动以来最慢一次工具耗时 |
| `rss_kb` | 当前进程 RSS，看内存泄漏最直接 |

## 安全

按 `mcp-builder` 的最佳实践补齐了三道闸：

1. **DNS rebinding 防护**：默认拒绝一切带 `Origin` 的浏览器请求（403 并提示加 `--allow-origin`）。
   局域网 IP 上的 MCP 若对任意 Origin 开放，受害者打开一个网页就能 POST `/mcp` 调工具——
   `Origin` 只限制浏览器读取响应，不限制发出请求。curl / SDK / `mcp-remote` 不带 `Origin`，不受影响。
2. **可选 Token**：`--http-token` 或 `CURL2PY_MCP_TOKEN`，走 `hmac.compare_digest` 比较；
   `/health` 刻意不拦，否则容器探针会被判死。
3. **文件读取收口**：`file_to_base64` 默认不注册；开启后仍要求绝对路径，
   设了 `--local-file-root` 就只允许该目录（`realpath` + `commonpath`，防 `..` 穿越）。
4. **Cookie 收藏夹隔离**（v1.5.0 起）：收藏夹的 Cookie 是敏感凭据，模型不该有机会读到。
   `ToolboxClient` 对 `/api/vault` 前缀直接抛错，请求统一带 `X-Client-Name: curl2py-mcp`
   供后端识别并 403；即便这两层都被绕过，收藏夹接口仍要主密码换发的 `X-Vault-Token`，
   而数据库卷并未挂载到本容器。三层任一层失效都还有兜底。

## 工具注解

全部 21 个工具带 MCP `annotations`：`readOnlyHint:true`、`destructiveHint:false`、
`idempotentHint:true`、`openWorldHint:false`——它们都是无副作用的纯变换。
`run_python` 单独标成 `readOnlyHint:false / destructiveHint:true / openWorldHint:true`，
客户端可据此决定是否需要二次确认。

## 压力测试与容量（S805 / armv7 实测）

复现：`python _stress_mcp.py --fast --abuse`（混合负载阶梯 + 异常注入），
`python _stress_mcp.py --levels 8 --long 90`（长稳）。

1.3.0 相对 1.2.0 的对比（同一台设备、同一台压测机、同样的负载组合）：

| 并发 | 1.2.0 RPS / p95 / 错误 | 1.3.0 RPS / p95 / 错误 |
|---|---|---|
| 8 | 61.3 / 202 ms / 0 | 60.3 / 201 ms / 0 |
| 16 | 57.2 / 1091 ms / **4 RST** | 62.2 / 342 ms / **0** |
| 32 | 47.7 / 3033 ms / **17 RST** | 55.7 / 902 ms / **0** |
| 64 | 33.3 / 3228 ms / **100 RST** | 37.2 / 1971 ms / **0** |
| 128 | —（连不上） | 64.5 / 2087 ms / **0** |

1.2.0 在并发 16 就开始吐 `ConnectionReset`：`socketserver` 的 `request_queue_size`
默认是 5，SYN 队列直接溢出。1.3.0 把它提到 128，并给线程池加了闸（超限回 `503`，
不再让连接被内核掐掉）。

长稳 90 秒 / 5301 请求：58.8 rps、p50 133 ms、p95 249 ms、**0 错误**；
跑满 10,129 请求后容器内存 12.3 MiB、进程 RSS 18 MB，无增长趋势。

异常注入 13/13：畸形 JSON、空 body、超长文本、未知工具、缺参、错类型、错路径、
`GET /mcp`、`DELETE` 会话——全部按预期拒绝，且响应都是 JSON（不再有 HTML 错误页）。

### 瓶颈在哪

同一时刻直接压后端 `9997` 的 `/api/format/json`：单请求 29 ms，32 并发能到 **211 rps**。
而 MCP 端到端 60 rps 就封顶，说明瓶颈不在后端，在 MCP 进程本身——单核容器 + Python GIL。
这不是靠代码能绕过去的：真要继续提，得换多 worker（gunicorn/uvicorn）或加机器。

所以这里的目标定为「**不崩、不雪崩、可观测**」，而不是追高吞吐：
背压回 503、keep-alive 不污染、连接复用省 28% 后端往返，这三件事在单核上是能拿到的全部收益。

### 1.4.0 复测：那 4 次超时是压测器自己的锅

120 秒长稳出现过 `0 错误` 与 `4~6 次客户端 TimeoutError` 交替的怪现象：服务端计数
`errors` 一直是 0，也没有一个 503。查下来是压测脚本每个请求都新建 TCP，120 秒 6400 个连接
在**压测机**上堆了 **4873 个 TIME_WAIT**，把 Windows 的动态端口池打满，之后 SYN 发不出去
就报 20 秒超时。服务端从头到尾没参与。

把压测器改成「每线程复用一条连接」（真实 MCP 客户端就是 keep-alive 行为，`MCP_FRESH_CONN=1`
可退回旧行为做对比）后：

| 指标 | 每请求新建连接 | 连接复用 |
|---|---|---|
| 请求数（120 s @ 并发 8） | 6,441 | **10,407** |
| RPS | 49.4 | **86.6** |
| p50 / p95 / p99 | 129 / 235 / 357 ms | **93 / 170 / 221 ms** |
| 错误 | 6（全为 TimeoutError） | **0** |
| 压测机 TIME_WAIT | 4,873 | **9** |

结论：吞吐不是被服务端压住的，是握手和端口耗尽的代价。这也说明**客户端该复用连接**——
真机上的 MCP 客户端本来就是这么做的，所以线上表现比早期压测数字更好。

## 容器部署（玩客云实测）

镜像复用 Web 镜像，`--entrypoint python` 覆盖入口，不额外构建。线上用的就是仓库里的
[`run-mcp.sh`](./run-mcp.sh)，直接 `sh run-mcp.sh` 即可（重复执行安全）：

```bash
docker run -d --name curl2py-mcp \
  --restart unless-stopped --init \
  -p 9999:9999 \
  --memory 128m --cpus 1 --read-only --tmpfs /tmp \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=2 \
  -v /root/mcp-files:/files:ro \
  -e CURL2PY_BASE_URL=http://192.168.0.107:9997 \
  --entrypoint python \
  curl2py-web:0.4.8-armv7 /app/mcp/server.py \
    --transport http --host 0.0.0.0 --port 9999 \
    --max-concurrency 64 \
    --allow-local-file --local-file-root /files
```

实测（S805 / armv7）：空载内存 **15.4 MiB**、CPU 0.03%，压测 1 万请求后无增长，
`/health` 3 秒内可读，版本 `1.4.0`，`tools: 22`。

`docker-compose.yml` 已含 `curl2py-mcp` 服务，`docker compose up -d` 会一并启动。
compose 网络内后端地址应写服务名：`CURL2PY_BASE_URL=http://curl2py:5000`。

看门狗装一次就长期生效：

```bash
printf '*/2 * * * * root /bin/sh /root/mcp-watchdog.sh\n' > /etc/cron.d/curl2py-mcp-watchdog
```

## 客户端配置

### stdio（单机，最省事）

```json
{
  "mcpServers": {
    "curl2py": {
      "command": "D:/conde3.10/python.exe",
      "args": ["C:/Users/Song/.agent/mcps/curl2py-mcp/server.py",
               "--base-url", "http://192.168.0.107:9997"]
    }
  }
}
```

### 远程 HTTP（局域网任何设备）

客户端支持 Streamable HTTP 时直接填 `http://192.168.0.107:9999/mcp`；
不支持时用 `mcp-remote` 桥接：

```json
{
  "mcpServers": {
    "curl2py-remote": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "http://192.168.0.107:9999/mcp"]
    }
  }
}
```

## 工具清单（21 个基础 + 2 个可选）

| 分组 | 工具 |
|---|---|
| 探活 | `toolbox_health` `toolbox_catalog` |
| 格式化 | `json_format` `header_to_dict` `cookie_to_dict` `python_dict_parse` `javascript_clean` `html_clean` |
| URL | `url_parse` `url_encode` `url_decode` |
| 转换 | `curl_to_python` `curl_to_feapder` |
| 加解密 | `base64` `hash` `crypto_operations` `crypto_process` |
| 对比 | `text_diff` `json_diff` |
| 时间 | `timestamp_to_date` `date_to_timestamp` |

按需开启：`run_python`（`--allow-script-run`）、`file_to_base64`（`--allow-local-file`）。

## 功能覆盖：后端每个能力都有对应工具

后端 22 个业务端点，MCP 侧一一对应；3 个 legacy 别名路由（`/convert/*`）是给老页面用的，不单独建工具。

| 后端端点 | MCP 工具 | 线上状态 |
|---|---|---|
| `/health` | `toolbox_health` | ✅ |
| `/api/tools` | `toolbox_catalog` | ✅ |
| `/api/convert/curl2python` | `curl_to_python` | ✅ |
| `/api/convert/curl2feapder` | `curl_to_feapder` | ✅ |
| `/api/run/python` | `run_python` | ⛔ 后端 `ENABLE_SCRIPT_RUNNER=false`，见下 |
| `/api/format/json` | `json_format` | ✅ |
| `/api/format/header` | `header_to_dict` | ✅ |
| `/api/format/cookie` | `cookie_to_dict` | ✅ |
| `/api/format/dict` | `python_dict_parse` | ✅ |
| `/api/format/javascript` | `javascript_clean` | ✅ |
| `/api/format/html` | `html_clean` | ✅ |
| `/api/url/parse` `/encode` `/decode` | `url_parse` `url_encode` `url_decode` | ✅ |
| `/api/crypto/base64` | `base64` | ✅ |
| `/api/crypto/hash` | `hash` | ✅ |
| `/api/crypto/process` | `crypto_process` | ✅ |
| `/api/crypto/operations` | `crypto_operations` | ✅ |
| `/api/image/base64` | `file_to_base64` | ✅ 走本地文件，见下 |
| `/api/json/diff` | `json_diff` | ✅ |
| `/api/text/diff` | `text_diff` | ✅ |
| `/api/time/timestamp` `/datetime` | `timestamp_to_date` `date_to_timestamp` | ✅ |

两处「不是简单复制」的地方，说明理由：

1. **`/api/image/base64` 要 multipart 上传，模型没有文件句柄。** 所以 `file_to_base64` 改成读服务端
   本地文件 + `--local-file-root` 收口，能力对等（都产出 `data:<mime>;base64,...`），但入口更适合 Agent。
   容器里挂载 `-v /root/mcp-files:/files:ro`，AI 只能读这个目录。
   差异：网页版一次能传 10 张，MCP 一次一张，多张就多调几次——单张上限同为 5MB。
2. **`run_python` 不是漏了，是被后端开关关掉了。** 后端返回 403 `脚本运行功能未启用`，
   这时候把它注册进去只会让模型对着一个必失败的工具反复重试。所以默认不注册，
   要用就三步：后端 `ENABLE_SCRIPT_RUNNER=true` + `CURL2PY_API_TOKEN` → MCP 加
   `--allow-script-run --http-token <值>` → 重启。**HTTP 下不给 Token 会直接拒绝启动**，
   因为那等于把远程命令执行挂到局域网上。

`/health` 的 `capabilities` 字段把这件事写明白了，运维不用翻启动参数：

```json
"capabilities": {"script_run": false, "local_file": true,
                 "local_file_root": "/files", "http_token": false, "max_concurrency": 64}
```

## 稳定性：六类故障各有归属

| 故障 | 谁兜底 | 实测 |
|---|---|---|
| 进程崩溃退出 | Docker `restart: unless-stopped` | `SIGSEGV` 杀死后 **9 秒**恢复，`RestartCount=1` |
| 容器被停 / 整个消失 | `/root/mcp-watchdog.sh`（cron 每 2 分钟） | `docker stop` 后看门狗 **9 秒**拉起 |
| 进程卡死但容器还在 | healthcheck → 看门狗重启 | Docker 本身**不会**重启 unhealthy 容器，这一层是补的空白 |
| 后端 9997 宕机 | 工具返回可读错误，MCP 自身不倒 | 返回「无法连接工具箱后端…Connection refused」，容器仍 healthy |
| 后端恢复 | 丢弃陈旧连接 + 重试一次 | 恢复后**第一次调用即成功**，不用重启 MCP |
| 日志写满磁盘 | `max-size=10m max-file=2` | 默认 json-file **不封顶**，长期跑必写满 6.9G 根分区 |
| 慢连接占死线程 | socket `timeout=30` + 并发上限 64 | 超限回 `503`，不是 RST |

自愈链条的日志在 `/var/log/mcp-watchdog.log`（自身也做 256KB 截断）。

## 三个刻意的设计

1. **对比类默认裁剪**：只回统计 + 统一 diff。浏览器用的 `visual_lines` 在 1000 行时 316 KB，
   原样返回直接撑爆上下文；要逐行明细得传 `detail=lines`，上限 800 行。
2. **`hash` 自动路由**：后端 `/api/crypto/hash` 只认 md5/sha1/sha256，SHA3/RIPEMD160/SM3
   走 `/api/crypto/process`。不路由的话模型算 SM3 必踩坑。
3. **错误不抛协议异常**：后端 `{success:false}` 转成 `isError:true` 的文本，模型能读懂并自行修正；
   协议错误只留给"工具不存在 / JSON 非法"这类客户端 bug。

## 排查

| 现象 | 原因 |
|---|---|
| 客户端看不到工具 | 没点「信任」/ 没重启客户端；stdio 模式下别把 stderr 合进 stdout |
| `initialize` 无响应 | 启动阶段有阻塞式登录逻辑，或端口被占 |
| HTTP 401 | 设了 `--http-token` 但客户端没带 `Authorization` |
| HTTP 405 on GET /mcp | 正常，本服务端不提供 SSE |
| 工具报"无法连接后端" | `--base-url` 写错；容器内不能写 `127.0.0.1`（那是容器自己） |

## 测试

```bash
python _test_mcp.py        # stdio  48 项
python _test_mcp_http.py   # http   74 项（含批量、Token、Origin、并发、错误码、能力自描述、危险组合守卫）
python _stress_mcp.py --fast --abuse   # 压测阶梯 + 异常注入 13 项
WITH_LOCAL_FILE=1 python _probe_tools.py   # 22 个工具全量真实调用（33 个用例）
```

四个脚本都自包含：自动拉起桩后端（8778）与服务端子进程。`_stress_mcp.py` 与
`_probe_tools.py` 直接打线上实例，可用 `MCP_HOST` / `MCP_PORT` 改目标；
`WITH_LOCAL_FILE=1` 时额外测 `file_to_base64`（含越界路径必须被拒），
样例文件路径用 `MCP_SAMPLE_FILE` 指定，默认 `/files/tiny.png`。

HTTP 那 74 项里有 24 项是压测与巡检逼出来的缺陷留下的回归用例：
4xx 后 keep-alive 复用、413 关连接、批量上限、`timestamp` 数字类型、
算法枚举、`title`、可观测字段、并发占满回 503、`capabilities` 自描述、
`--allow-script-run` 无 Token 拒绝启动。

## 与 mcp-builder 最佳实践的对照

| 要求 | 现状 |
|---|---|
| Streamable HTTP + stdio 双传输 | ✅ |
| 工具注解（readOnly / destructive / idempotent / openWorld） | ✅ 21 个全覆盖 |
| 工具 `title` 与参数 `enum` | ✅ 21 个标题；`hash`/`crypto_process`/`base64` 等算法参数带枚举 |
| 错误写在 result 里而非协议异常 | ✅ `isError:true` + 可读原因 |
| stdio 不污染 stdout | ✅ 日志一律 stderr |
| DNS rebinding 防护、Origin 校验 | ✅ 默认全拒 |
| 输入校验（尺寸、路径、编码） | ✅ 1MB/512KB/5MB 分级上限 + realpath 收口 |
| `initialize` 下发 instructions 引导 | ✅ |
| 传输层健壮性（backlog、并发闸、keep-alive 卫生） | ✅ 128 backlog / 64 并发 / 4xx 先 drain |
| 可观测性（计数、按工具耗时、RSS） | ✅ `/health` 全量暴露 |
| 分页 | 不适用（本项目无列表型工具） |
| 服务前缀命名（`curl2py_*`） | ⚠️ 未采用：21 个工具已随文档/配置/测试固化，改名会打断现有客户端；本服务端只暴露工具箱能力，冲突面小。若将来与别的服务端重名，加 `--prefix` 一次性迁移 |
