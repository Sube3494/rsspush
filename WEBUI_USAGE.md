# RSS推送插件 - WebUI配置指南

## 🎉 功能亮点

所有配置都可以在WebUI中完成，无需使用繁琐的机器人命令！

## 📖 使用步骤

### 1. 打开配置界面

1. 访问 AstrBot WebUI（通常是 `http://localhost:6185`）
2. 进入 **插件管理** 或 **插件配置** 页面
3. 找到 **RSS推送** 插件
4. 点击 **配置** 按钮

### 2. 添加RSS订阅

在配置界面的 **订阅列表 (subscriptions)** 部分：

1. 点击 **添加** 按钮
2. 填写订阅信息：
   - **名称** (name): 订阅的显示名称，如"B站动态"
   - **地址** (url): RSS订阅地址，如 `https://rsshub.app/bilibili/user/dynamic/123456`
   - **启用** (enabled): 勾选以启用自动推送
   - **单次推送条目数** (max_items): 每次推送最新的几条内容（1-10）
   - **自定义模板** (custom_template): 留空使用默认模板，或自定义消息格式

3. 点击 **保存**

### 3. 配置全局设置

#### 📡 轮询配置 (polling)
- **启用自动轮询**: 开启后自动检查RSS更新
- **轮询间隔**: 每多少分钟检查一次（5-1440分钟）

#### 📤 推送配置 (push)
- **默认单次推送条目数**: 全局默认值
- **批量推送间隔**: 多条内容之间的间隔秒数

#### 🌐 RSSHub配置 (rsshub)
- **默认实例地址**: RSSHub服务地址，如 `https://rsshub.app`

#### 🔄 其他配置
- **去重配置**: 防止重复推送
- **静默时段**: 设置不推送的时间段
- **频率限制**: 限制推送频率

### 4. 设置推送目标

配置UI创建的订阅需要指定推送到哪个会话：

1. 在要接收推送的聊天会话中（群聊或私聊）
2. 发送命令：
   ```
   /rss target add all
   ```
   这会将当前会话添加到所有订阅的推送目标

或者为单个订阅添加：
```
/rss target add 订阅名称
```

### 5. 测试订阅

在聊天会话中发送：
```
/rss test 订阅名称
```

立即推送该订阅的最新一条内容。

## 🎯 完整工作流

### 方案一：WebUI + 命令（推荐）

1. ✅ **在WebUI中添加订阅** - 填写URL、名称、模板
2. ✅ **在WebUI中配置轮询间隔** - 设置检查频率
3. ✅ **在聊天中设置推送目标** - `/rss target add all`
4. ✅ **完成** - 等待自动推送

### 方案二：纯命令（备选）

```bash
# 添加订阅（自动推送到当前会话）
/rss add https://rsshub.app/bilibili/user/dynamic/123456 B站动态

# 测试推送
/rss test B站动态

# 查看订阅列表
/rss list
```

## 📊 常用命令

```bash
# 查看所有订阅
/rss list

# 查看订阅详情
/rss info 订阅名称

# 立即检查更新
/rss update 订阅名称
/rss update all

# 查看推送统计
/rss stats
/rss stats 订阅名称

# 管理推送目标
/rss target add 订阅名称      # 添加当前会话
/rss target add all          # 添加到所有订阅
/rss target list 订阅名称     # 查看推送目标

# 查看帮助
/rss help
```

## 📝 自定义消息模板

在订阅的 **自定义模板** 字段中，可以使用以下变量：

- `{name}`: 订阅名称
- `{title}`: RSS条目标题
- `{link}`: RSS条目链接
- `{description}`: RSS条目描述
- `{pubDate}`: 发布时间
- `{author}`: 作者

示例：
```
🎉 {name} 更新啦！

📰 {title}
👤 作者：{author}
🕐 {pubDate}

{description}

👉 查看详情：{link}
```

## 💡 实用技巧

### 1. RSSHub快捷方式
如果URL以 `/` 开头，会自动添加RSSHub实例地址：
```
/rss add /bilibili/user/dynamic/123456 B站动态
```
等同于：
```
/rss add https://rsshub.app/bilibili/user/dynamic/123456 B站动态
```

### 2. 批量管理
在WebUI中可以：
- 批量添加多个订阅
- 统一设置推送间隔
- 一键启用/禁用订阅

### 3. 多会话推送
同一个订阅可以推送到多个会话：
```bash
# 在群A中
/rss target add 订阅名称

# 在群B中
/rss target add 订阅名称
```

## 🔧 故障排查

### 订阅不推送？

1. **检查订阅是否启用**
   - WebUI配置中，订阅的 `enabled` 应为 `true`
   - 或使用命令：`/rss enable 订阅名称`

2. **检查是否有推送目标**
   ```bash
   /rss target list 订阅名称
   ```
   如果没有，添加：
   ```bash
   /rss target add 订阅名称
   ```

3. **检查轮询是否启用**
   - WebUI配置 → polling → enabled 应为 `true`

4. **手动测试**
   ```bash
   /rss test 订阅名称
   ```

### 配置未生效？

- ⚠️ **修改配置后需要重启 AstrBot**
- 或使用插件重载功能（如果支持）

## 🌟 推荐配置

### 新闻类订阅
- 轮询间隔：10-15分钟
- 单次推送条目数：3-5条
- 启用去重

### 博客类订阅
- 轮询间隔：30-60分钟
- 单次推送条目数：1-2条
- 启用静默时段

### 社交媒体类
- 轮询间隔：5-10分钟
- 单次推送条目数：5-10条
- 启用频率限制

## 🎊 享受推送吧！

现在您可以轻松管理所有RSS订阅，再也不用频繁使用命令了！

如有问题，请使用 `/rss help` 查看完整帮助。

