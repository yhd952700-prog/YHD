# LiuHao AI OS - PHP SDK

官方 LiuHao AI OS 平台 PHP SDK。

## 特性

- Guzzle HTTP 客户端
- 关联数组风格的响应数据
- 指数退避重试机制
- 完整的异常层次结构
- 支持 Agents、Memory、Knowledge API
- PHP 7.4+ 兼容
- PSR-4 自动加载

## 安装

使用 Composer 安装：

```bash
composer require liuhao/liuhao-sdk
```

## 快速开始

```php
<?php
require 'vendor/autoload.php';

use LiuHao\SDK\Client;

// 创建客户端
$client = new Client('your-api-key');

// 列出所有 Agent
$agents = $client->agents()->list();
foreach ($agents as $agent) {
    echo $agent['name'] . ' - ' . $agent['status'] . PHP_EOL;
}

// 创建 Agent
$agent = $client->agents()->createSimple('my-agent', 'gpt-4');
echo "Created agent: " . $agent['id'] . PHP_EOL;

// 执行 Agent
$result = $client->agents()->execute($agent['id'], 'Hello, how are you?');
echo $result . PHP_EOL;
```

## 配置选项

```php
<?php
use LiuHao\SDK\Config;
use LiuHao\SDK\Client;

$config = (new Config())
    ->setApiKey('your-api-key')
    ->setBaseUrl('https://api.liuhao.ai/v1')
    ->setTimeout(30)
    ->setMaxRetries(3)
    ->setInitialRetryDelayMs(1000)
    ->setRetryBackoffMultiplier(2.0)
    ->setUserAgent('MyApp/1.0');

$client = new Client($config);
```

## Memory API

```php
<?php
// 存储记忆
$memory = $client->memory()->storeWithOptions(
    'User prefers dark mode',
    'preference',
    'ui',
    0.8
);

// 搜索记忆
$results = $client->memory()->search('dark mode');
echo "Found " . count($results) . " memories" . PHP_EOL;

// 列出记忆
$allMemories = $client->memory()->list();
```

## Knowledge API

```php
<?php
// 创建知识库
$kb = $client->knowledge()->createBase(
    'my-docs',
    'My documentation knowledge base'
);

// 上传文档
$client->knowledge()->uploadDocument(
    $kb['id'],
    'README.md',
    '# My Project\n...'
);

// 搜索知识库
$results = $client->knowledge()->search($kb['id'], 'how to install');
foreach ($results as $result) {
    echo "Score: {$result['score']}, Content: {$result['content']}" . PHP_EOL;
}
```

## 异常处理

SDK 提供完整的异常层次结构：

- `LiuHaoException` - 基类异常
- `AuthenticationException` - 认证失败 (401)
- `NotFoundException` - 资源不存在 (404)
- `ValidationException` - 请求验证失败 (400)
- `RateLimitException` - 速率限制 (429)
- `APIException` - 服务端错误 (5xx)
- `TimeoutException` - 请求超时

```php
<?php
use LiuHao\SDK\Exceptions\NotFoundException;
use LiuHao\SDK\Exceptions\RateLimitException;
use LiuHao\SDK\Exceptions\LiuHaoException;

try {
    $agent = $client->agents()->get('nonexistent-id');
} catch (NotFoundException $e) {
    echo "Agent not found: " . $e->getMessage() . PHP_EOL;
} catch (RateLimitException $e) {
    echo "Rate limited, retry after " . $e->getRetryAfterSeconds() . "s" . PHP_EOL;
} catch (LiuHaoException $e) {
    echo "API error: " . $e->getMessage() . PHP_EOL;
    echo "Status: " . $e->getStatusCode() . PHP_EOL;
    echo "Request ID: " . $e->getRequestId() . PHP_EOL;
}
```

## 重试策略

SDK 默认对以下情况进行重试（最多 3 次）：

- 网络错误（超时、连接失败）
- 速率限制 (429)
- 服务端错误 (502, 503, 504)

重试采用指数退避策略，初始延迟 1 秒，每次重试延迟翻倍。

## 许可证

详见项目根目录的 LICENSE 文件。
