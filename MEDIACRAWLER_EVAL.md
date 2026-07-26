# MediaCrawler 接入评估

**评估日期**: 2026-07-26
**目标 repo**: https://github.com/NanmiCoder/MediaCrawler
**作者**: NanmiCoder
**规模**: 57.4k ⭐ / 11.5k fork / 792 commits（活跃维护）

---

## MediaCrawler 是什么

基于 Playwright 的多平台社交媒体爬虫，支持 7 个中文平台：

| 平台 | 覆盖 | 与 aiseclect 现有重叠 |
|------|------|----------------------|
| 小红书 | ✅ | `feedgrab` 已支持 xhs（轻量级） |
| 抖音 | ✅ | ❌ 无 |
| 快手 | ✅ | ❌ 无 |
| B 站 | ✅ | ❌ 无 |
| 微博 | ✅ | `newsnow` 已支持 weibo（聚合源） |
| 百度贴吧 | ✅ | ❌ 无 |
| 知乎 | ✅ | `zhihu_ai_collector` 已支持 AI 话题 |

**技术栈**: Python + Playwright（CDP 模式复用本地 Chrome）+ FastAPI WebUI + JS signing 提取（无加密逆向）

---

## 与 aiseclect 的契合度

### 价值
1. **覆盖 XHS pillar 真实热点**：现在 XHS 草稿是「AI 新闻翻译成 XHS 语气」，缺真实的 XHS 在聊什么
2. **多平台聚合**：抖音 / 快手 / B 站 / 贴吧现在是空白
3. **二级评论 + 创作者主页**：能拿到真实用户痛点，喂给 XHS 「教程 / 避坑 / 测评」pillar

### 风险（重要）
1. **🔴 需要登录**：QR 码扫码或 cookie，每几天失效一次，维护成本高
2. **🔴 国内合规风险**：README 自己写了「不保证不违法」「国内已有多起爬虫判例」—— 真出问题时不能甩锅给上游
3. **🟠 Playwright 重依赖**：Chromium 下载 + 启动慢（30s+），每 4h cron 跑不划算；只在按需触发时跑
4. **🟠 账号封禁**：多账号轮换 / IP 代理池是付费 Pro 版的功能
5. **🟡 与现有 feedgrab 重叠**：feedgrab 已覆盖 mpweixin/xhs/ytb/reddit，重复投入

---

## 建议：**不接主路径，提供可选适配器**

理由：
- **风险/收益不合算**直接接主路径
- 但作为「可选第 13 路 CLI 工具」（类似 last30days / feedgrab），用户自己承担风险启用，是低风险折中方案
- 安装与否由 `MEDIACRAWLER_BIN` 环境变量决定；不安装则节点变成 no-op

### 接入方案

**改动**：
- 新增 `src/tools/mediacrawler.py`：subprocess wrapper + JSON 解析
- 新增 `src/graphs/nodes/mediacrawler_collector_node.py`：collector 节点
- `src/graphs/state.py`：加 `mediacrawler_materials` 字段 + IO 类
- `src/graphs/graph.py`：fan-out 加 1 条边
- `src/graphs/nodes/material_merge_node.py`：合并
- `tests/test_mediacrawler.py`：测试
- `pyproject.toml`：**不**加依赖（CLI 调用，无 Python 依赖）
- `.env.example`：文档

**不接入的边界**：
- ❌ 不做自动登录（用户手动 QR 码）
- ❌ 不内置 cookie 池（用户自管理）
- ❌ 不强制启用（默认 no-op）

---

## 决策点（请用户确认）

| 选项 | 含义 | 推荐度 |
|------|------|--------|
| A | 完全不接 | ⭐⭐⭐⭐ 安全 |
| B | 接可选适配器（MEDIACRAWLER_BIN 环境变量启用） | ⭐⭐⭐ 折中（推荐） |
| C | 接主路径（每次 cron 必跑） | ❌ 不推荐 |

如果选 B，下一步我会写 ~150 行 wrapper 代码 + 12 个 pytest 测试，不影响当前 163 个测试。