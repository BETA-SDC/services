# BETA SDC Services

BETA SDC 的服务代码和部署入口，统一管理账号、个人主页、组织权限、活动签到、
系统邮件以及日历服务的版本。生产数据和凭据不属于本仓库。

本机源码路径：`/Users/jietexue/20_westlake/BETA-SDC/services/`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `server/registration/` | Flask 账号服务、登录和通行密钥、资料、GitHub 组织权限、签到与 PAT |
| `server/mailer/` | 邮件队列、验证码和系统通知 |
| `server/` | 测试、发布清单、备份工具、Linux 部署脚本与 systemd/Nginx 模板 |
| `campus-register/` | 各站共享模板、账号和签到前端；另含已停用的早期 macOS 部署工具 |
| `calendar/` | 日历服务子模块，来源为 `BETA-SDC/calendar`，固定到已验证的提交 |
| `duckdns/` | DuckDNS 更新工具 |
| `client/macos/` | 早期本机 DNS 配置工具 |

```sh
git clone --recurse-submodules https://github.com/BETA-SDC/services.git
cd services
```

已有检出可运行 `git submodule update --init --recursive`。日历活动源数据及其
GitHub Pages 工作流继续由日历仓库维护；本仓库通过子模块提交号管理日历版本，
不复制或合并两边的历史。

## 功能

- 学校邮箱验证码注册，注册时必填姓名和学号；支持邮箱登录及通行密钥登录。
- 可选 GitHub 绑定，读取已授权的账号、组织和 Team 信息。
- 各站共享菜单和颜色偏好，使用独立站点会话进行 SSO。
- 成员创建签到表、关联日历、生成二维码；管理员管理签到表和删除申请。
- 名单按权限查看；电话、证件等隐私信息的查看或导出需要限时 PAT 和相应验证。
- 系统邮件、加密资料存储、数据库备份及 Linux 服务隔离。

密码找回尚未实现。现有部署仍包含历史入口和 macOS 工具，不能把旧安装脚本当作
通用的一键部署器运行。

## 本地验证

使用 Python 3.11 或更新版本以及支持 Node 原生测试运行器的 Node.js：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install Flask gunicorn \
  -r server/requirements-private.txt -r server/requirements-checkin.txt
.venv/bin/python -B -m unittest discover -s server -t .
node --test campus-register/tests/*.test.mjs
.venv/bin/python -B server/release_manifest.py
```

测试使用临时数据，不需要生产 SMTP、GitHub OAuth 或 DuckDNS 凭据。日历的构建
和测试方法见 `calendar/README.md` 与 `calendar/AGENTS.md`。

## 部署与数据

当前采用 Linux、Gunicorn、Nginx 和 SQLite。应用文件、运行状态和凭据分别放置，
源码更新不会自动替换数据库、私密资料加密密钥或 TLS 证书。

`server/deploy-service-upgrade.sh` 是现有部署的升级入口，包含备份、检查及代码
回滚；它有部署环境约束，迁移到其他主机前必须阅读并调整相应配置。公开仓库的
更新不会自动部署到生产服务器。

仅升级 550c 上已有的日历服务时，使用日历子模块的升级脚本，不运行账户服务的
全量升级入口。在服务器的本仓库检出目录执行：

```sh
git submodule update --init calendar
sudo sh calendar/deploy/upgrade-existing.sh \
  "$PWD/calendar" "$(git -C calendar rev-parse HEAD)"
```

脚本备份并更新日历的 `server/`、`scripts/` 和 `web/`，等待 worker 发布指定
提交后重载 Web 服务，并检查 HTTPS 接口；失败时恢复代码和原发布快照。
不会重写账号数据库、部署凭据、Nginx 配置或监听端口。子模块版本更新需先验证
日历仓库，再提交本仓库中的子模块指针，不能仅更新子模块工作区而遗漏父仓库提交。

不要提交数据库、用户资料、备份、邮件队列、`.env`、SSH 私钥、SMTP 授权码、
OAuth secret、DuckDNS token 或证书私钥。`.gitignore` 已排除常见的运行文件；
提交前仍需检查差异及历史。发现安全问题时请发送不含真实凭据和用户资料的说明
到 `betasdc@163.com`。

## 文档

- [项目概述](OVERVIEW.md)
- [服务架构](server/ARCHITECTURE.md)
- [账号服务与部署说明](server/README.md)
- [签到与授权说明](server/CHECKIN.md)
- [第三方代码、图标与品牌说明](THIRD-PARTY.md)

公开可读不等于授予任意再分发许可；仓库尚未指定自身代码的统一许可证。第三方
依赖沿用其各自许可证，BETA 标识和吉祥物遵循上游品牌使用规则。
