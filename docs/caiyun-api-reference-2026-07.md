# 彩云天气套餐内 API 整理（2026-07）

整理日期：2026-07-25

资料来源：

- 彩云天气 v2.6 官方文档，页面最后更新时间为 2026-07-24。
- 当前账号“调用量套餐”页面截图。
- 当前项目 `adapters/caiyun.py`、`services/fusion.py` 与统一数据模型。

命令级调用次数、缓存和“和风优先、彩云全局融合”的方案见 [项目命令、API 调用与成本路由](./commands-api-mapping.md)。

## 1. 套餐边界

当前 Token 只确认支持套餐页面列出的以下调用方式：

| 套餐项目 | 请求形式 | 套餐范围 |
| --- | --- | --- |
| 综合天气 | `/v2.6/{token}/{lon},{lat}/weather` | 实况、小时、日级等综合响应；子数据块仍受权限限制 |
| 综合 JSONP | `/weather.jsonp?callback=...` | 浏览器脚本跨域回调；服务端 Bot 不需要 |
| 实况数据 | `/realtime` | 当前天气 |
| 未来小时预报 | `/hourly?hourlysteps=72` | 未来 72 小时 |
| 未来逐日预报 | 套餐页示例 `/daily?dailysteps=7` | 示例为 7 日，不足以证明硬上限；按次计费时建议请求 15 并记录实际返回长度 |
| 历史小时预报 | `/hourly?hourlysteps=72&begin={timestamp}` | 套餐页面标注为历史 24 小时 |
| 预警数据 | 天气接口附加 `alert=true` | 当前生效预警 |
| 生活指数 | `/daily?dailysteps={days}` | 套餐标注 4 项；可随逐日请求一次取回 |

所有示例必须使用 `{token}` 占位符。套餐页面或截图中的真实 Token 不得写入文档、测试、日志或提交记录。

## 2. 当前套餐明确不应依赖的接口

以下接口虽然存在于彩云官方总目录，但当前套餐没有确认权限：

- 独立分钟级降水 `/minutely`。
- 雷达图、单站雷达、分钟降水网格和雷达灰度图。
- v3 丰富生活指数。
- v3 预警、CAP 预警与 Webhook。
- v3 空气质量站点预报。
- 台风、潮汐、海浪、天文、模式网格、高空变量、40 天预报、朝晚霞。
- 土壤、闪电、高精度温度、逆地理编码等其他增值产品。

公共综合接口文档说明：Token 没有分钟权限时，整个 `result.minutely` 块不会返回。因此当前项目必须继续使用和风分钟降水，不能把彩云视为可用的分钟增强源。

## 3. 基础请求约定

### 3.1 稳定版本与地址

- v2.6：Stable，当前套餐使用的版本。
- API 根地址：`https://api.caiyunapp.com`。
- URL 路径参数顺序：`经度,纬度`。
- 响应 `location` 顺序：`[纬度, 经度]`，与 URL 相反。

推荐使用官方当前文档中的无扩展名路径：

```text
GET https://api.caiyunapp.com/v2.6/{token}/{lon},{lat}/weather
```

项目已经使用文档明确列出的 `/weather`；JSONP 仅使用 `/weather.jsonp`，Bot 不接入。

### 3.2 通用响应元数据

| 字段 | 含义 |
| --- | --- |
| `status` | 请求状态，成功为 `ok` |
| `api_version` | API 版本 |
| `api_status` | 服务状态 |
| `lang` | 返回语言 |
| `unit` | 单位制 |
| `tzshift` | 目标地点时区偏移秒数 |
| `timezone` | IANA 时区 |
| `server_time` | 服务器 Unix 时间戳 |
| `location` | `[纬度, 经度]` |
| `result` | 各业务数据块 |

项目目前没有保存 `timezone/tzshift/server_time`，分钟或历史时间处理时不能简单使用服务器本地 `datetime.now()`。

### 3.3 通用查询参数

| 参数 | 当前套餐用法 | 说明 |
| --- | --- | --- |
| `lang` | 可选 | `zh_CN/zh_TW/en_US/en_GB/ja` |
| `unit` | 建议固定 `metric:v2` | 温度 °C、风速 km/h、降水强度 mm/h |
| `alert` | 需要预警时传 `true` | Token 具有预警权限才返回 `result.alert` |
| `callback` | Bot 不使用 | JSONP 回调函数名 |
| `hourlysteps` | 最多按套餐使用 72 | 公开接口理论上支持更长，但套餐会截断 |
| `dailysteps` | 建议请求 15 | 公共文档通常支持未来 15 日；实际长度由套餐决定，超出时服务端按套餐上限返回 |
| `begin` | 历史模板使用 | 套餐页面提供，公开小时文档未列出 |

## 4. 综合天气接口

### 4.1 请求

```text
GET /v2.6/{token}/{lon},{lat}/weather?alert=true&dailysteps=15&hourlysteps=72&unit=metric:v2
```

### 4.2 响应数据块

| JSONPath | 当前套餐处理方式 |
| --- | --- |
| `result.realtime` | 可用，映射实时天气 |
| `result.minutely` | 当前套餐无独立分钟权限，必须允许整块缺失 |
| `result.hourly` | 可用，最多按 72 小时使用 |
| `result.daily` | 可用；请求 15，保存实际返回长度以确认权益 |
| `result.alert` | `alert=true` 且权限有效时可用 |
| `result.forecast_keypoint` | 可用于展示供应商摘要，不能当作原始数值 |
| `result.primary` | 当前文档恒为 0，无业务价值 |

`result.hourly.description` 是未来 24 小时天气变化文案；`result.forecast_keypoint` 可能来自分钟或小时逻辑。当前套餐没有分钟权限时，应预期它主要使用小时文案，但仍以实际响应为准。

## 5. 综合 JSONP

套餐页面提供：

```text
GET /weather.jsonp?callback=MYCALLBACK
```

JSONP 用于浏览器 `<script>` 跨域加载。Telegram Bot 使用服务端 HTTP 客户端，应继续请求 JSON，不应接入 JSONP，也不能执行返回的回调脚本。

## 6. 实况数据

### 6.1 请求

```text
GET /v2.6/{token}/{lon},{lat}/realtime?unit=metric:v2
```

### 6.2 主要字段

| JSONPath `result.realtime.*` | 含义与单位 | 当前项目 |
| --- | --- | --- |
| `temperature` | 2 m 气温，°C | 已映射 |
| `apparent_temperature` | 原生体感温度，°C | 已映射；缺失时不应复制气温 |
| `humidity` | 相对湿度，0~1 | 已转百分比 |
| `cloudrate` | 总云量，0~1 | 已转百分比并映射 |
| `skycon` | 天气现象枚举 | 已映射 |
| `visibility` | 水平能见度，km | 已映射 |
| `dswrf` | 向下短波辐射，W/m² | 已映射 |
| `wind.speed` | 10 m 风速，km/h | 已与风力等级分开映射 |
| `wind.direction` | 风向角，0~360° | 已按角度字段映射 |
| `pressure` | 地面气压，Pa | 已除以 100 转为 hPa |
| `precipitation.local.datasource` | 当前降水数据源 | 未映射 |
| `precipitation.local.intensity` | 当前降水强度，mm/h | 已映射但展示误标为 mm |
| `precipitation.nearest.distance` | 最近降水带距离，km | 未映射；仅雷达覆盖区出现 |
| `precipitation.nearest.intensity` | 最近降水带强度，mm/h | 未映射 |

### 6.3 实时空气质量

| 字段 | 含义 |
| --- | --- |
| `air_quality.pm25` | PM2.5，μg/m³ |
| `air_quality.pm10` | PM10，μg/m³ |
| `air_quality.o3` | O₃，μg/m³ |
| `air_quality.so2` | SO₂，μg/m³ |
| `air_quality.no2` | NO₂，μg/m³ |
| `air_quality.co` | CO，mg/m³ |
| `air_quality.aqi.chn` | 中国标准 AQI |
| `air_quality.aqi.usa` | 美国标准 AQI |
| `air_quality.description.chn/usa` | 官方分类描述 |

项目已保存中国 AQI、PM2.5、PM10、O₃、SO₂、NO₂ 和 CO；彩云没有提供首要污染物时保持为空，不再推断。

### 6.4 实时生活指数

实况块通常包含：

- `life_index.ultraviolet`：实况紫外线指数。
- `life_index.comfort`：实况舒适度。

套餐标注的“4 项生活指数”以日级接口为准，不应假定 4 项都会出现在实况块。

## 7. 未来 72 小时预报

### 7.1 请求

```text
GET /v2.6/{token}/{lon},{lat}/hourly?hourlysteps=72&unit=metric:v2
```

### 7.2 小时字段

每个数组条目使用目标地点时区 ISO 时间，各数组应按 `datetime` 对齐：

| 字段 | 含义 | 当前项目 |
| --- | --- | --- |
| `temperature[].value` | 气温，°C | 已映射 |
| `apparent_temperature[].value` | 原生小时体感，°C | 已映射，是彩云最重要的补充字段 |
| `skycon[].value` | 天气现象 | 已映射；缺失时不应默认晴 |
| `precipitation[].value` | 降水强度，mm/h | 已映射但目前误写成 mm |
| `precipitation[].probability` | 降水概率，0~100% | 已映射 |
| `wind[].speed` | 风速，km/h | 已映射 |
| `wind[].direction` | 风向角 | 已映射 |
| `humidity[].value` | 相对湿度，0~1 | 已转百分比 |
| `cloudrate[].value` | 云量，0~1 | 已转百分比 |
| `pressure[].value` | 气压，Pa | 已除以 100 转为 hPa |
| `visibility[].value` | 能见度，km | 已映射 |
| `dswrf[].value` | 向下短波辐射，W/m² | 已映射 |
| `air_quality.aqi[].value.chn/usa` | 小时 AQI | 已映射中国标准值 |
| `air_quality.pm25[].value` | 小时 PM2.5 | 已映射 |
| `description` | 未来 24 小时自然语言描述 | 未单独保存 |

和风未来小时没有 `feelsLike`。融合时只能用彩云明确返回的 `apparent_temperature` 补充，缺失时直接不显示。

## 8. 逐日预报与 4 项生活指数

### 8.1 请求

```text
GET /v2.6/{token}/{lon},{lat}/daily?dailysteps=15&unit=metric:v2
```

### 8.2 日级字段组

| 字段组 | 内容 | 当前项目 |
| --- | --- | --- |
| `temperature` | 全天 max/min/avg | 只映射 max/min |
| `temperature_08h_20h` | 白天 max/min/avg | 未映射 |
| `temperature_20h_32h` | 夜间 max/min/avg | 未映射 |
| `precipitation` | 全天 max/min/avg/probability，单位 mm/h | 只取 max 或 avg，且误标为 mm |
| `precipitation_08h_20h` | 白天降水统计和概率 | 未映射 |
| `precipitation_20h_32h` | 夜间降水统计和概率 | 未映射 |
| `wind` | 全天风速/风向 max/min/avg | 未映射 |
| `wind_08h_20h` | 白天风统计 | 未映射 |
| `wind_20h_32h` | 夜间风统计 | 未映射 |
| `humidity/cloudrate/pressure/visibility/dswrf` | 日级 max/min/avg | 仅湿度 avg 已映射 |
| `air_quality.aqi/pm25` | 日级 max/min/avg | 未映射 |
| `skycon` 与昼夜 skycon | 全天、白天、夜间主要天气 | 已映射昼夜 |
| `astro` | 日出、日落 | 已映射 |

当天的全天 `avg` 可能只统计从当前整点到当天结束，并不一定代表完整 24 小时；昼夜窗口和风均值除外。展示时必须保留统计窗口语义。

### 8.3 套餐内 4 项生活指数

套餐页面只承诺 4 项，建议仅依赖：

| key | 名称 |
| --- | --- |
| `dressing` | 穿衣指数 |
| `carWashing` | 洗车指数 |
| `coldRisk` | 感冒指数 |
| `ultraviolet` | 紫外线指数 |

每项日级条目包含 `date/index/desc`。公共日级样例还出现 `comfort`，但套餐只标注 4 项，因此 `comfort` 只能作为条件出现的可选字段，不能作为套餐保证能力。

项目已改为从 `daily.life_index` 读取套餐承诺的穿衣、洗车、感冒和紫外线 4 项；和风已有同类型时仍保持和风优先。

## 9. 历史 24 小时预报

套餐页面给出的形式为：

```text
GET /v2.6/{token}/{lon},{lat}/hourly?hourlysteps=72&begin={Unix时间戳}
```

注意：

- 当前公开的 v2.6 小时文档没有列出 `begin`，官方文档站内搜索也没有结果。
- 彩云官方 MCP 页面提供 `get_historical_weather`，明确描述为过去 24 小时，和套餐页面能力相互印证。
- 该参数应视为当前账号套餐提供的专属调用模板，不能推广成公开稳定契约。
- 正式接入前需要用非生产测试地点验证：`begin` 的时区、允许回溯范围、返回起止点、72 个条目中历史与未来的分界、额度计费方式。
- 未验证前不能本地拼接或伪造历史数据。

## 10. 预警数据

### 10.1 请求

任意套餐内天气接口均可附加：

```text
alert=true
```

### 10.2 主要字段

| JSONPath `result.alert.*` | 含义 | 当前项目 |
| --- | --- | --- |
| `status` | 数据块状态 | 未保存 |
| `content[].title` | 预警标题 | 已映射 |
| `content[].code` | 4 位预警类型+级别代码 | 被映射为 type |
| `content[].status` | 生效状态，通常为“预警中” | 当前错误地映射为 level |
| `content[].description` | 预警正文 | 已映射 |
| `content[].pubtimestamp` | 发布时间 Unix 秒 | 已映射 |
| `content[].source` | 官方来源 | 当前被固定写成 Caiyun |
| `content[].province/city/county/location` | 行政区和地点 | 未保存 |
| `content[].adcode/regionId` | 地区代码 | 未保存 |
| `content[].alertId` | 预警唯一 ID | 未保存 |
| `adcodes` | 请求点的行政区数组 | 未保存 |

`code` 前两位是类型，后两位是等级：`00/01/02/03/04` 对应白/蓝/黄/橙/红。按官方编码表转换是确定性枚举映射，不属于气象估算。

彩云 v2 预警处于维护状态，但它是当前套餐确认可用版本；没有升级套餐前不要设计 v3 Webhook 依赖。

## 11. `metric:v2` 单位表

| 数据 | 单位 |
| --- | --- |
| 温度、体感 | °C |
| 风速 | km/h |
| 风向 | 从北顺时针 0~360° |
| 气压 | Pa |
| 相对湿度、云量 | 0~1 |
| 能见度、距离 | km |
| 实况降水强度 | mm/h |
| 小时降水值 | mm/h |
| 日级降水 max/min/avg | mm/h |
| `dswrf` | W/m² |

项目统一模型已通过 `precip_kind` 区分累计量 `amount/mm` 与强度 `intensity/mm/h`，并在解析时把彩云气压统一转换为 hPa。

## 12. 当前项目与套餐不一致处

| 项目实现 | 套餐事实 | 建议 |
| --- | --- | --- |
| 请求 `dailysteps=15` | 套餐页只展示 7 日示例，实际硬上限未确认 | 按次计费时先保留 15，并记录实际返回长度；不要仅凭示例降成 7 |
| 请求 `hourlysteps=72` | 套餐支持 72 小时 | 已实施，并记录实际返回长度 |
| 使用 `/weather` | 当前官方路径是 `/weather` | 已实施 |
| 功能描述为“彩云小时体感/补充数据增强” | 套餐没有分钟权限 | 已实施 |
| 忽略 `result.minutely` | 无权限时整块不返回 | 已实施，和风分钟数据不会被覆盖 |
| 实时体感缺失时保持 `None` | 不是官方体感值 | 已实施 |
| 预警状态与等级分离 | `status` 只是生效状态 | 已按 `code` 级别码映射 |
| 降水强度显示为 `mm/h` | 彩云是强度 | 已与和风累计量拆分 |

## 13. 套餐内推荐调用方案

### 常规查询

- 和风继续负责 GeoAPI 和基础天气。
- 彩云综合接口参与全局融合，统一请求 `hourlysteps=72&dailysteps=15&alert=true&unit=metric:v2`；原始响应按坐标缓存 1 小时并进行进程内并发去重。
- 从彩云提取未来小时原生体感、补充小时 AQI/PM2.5、能见度和辐射。
- 不使用或等待 `minutely`；和风分钟降水继续作为唯一可用分钟源。

### 降雨提醒

- 中国区域：使用和风每 5 分钟累计降水和雨雪类型。
- 彩云小时降水概率可作为 2~6 小时趋势补充，不能伪装成分钟概率。
- 海外没有分钟数据时明确降级到小时预报。

### 历史查询

- 彩云只做套餐提供的过去 24 小时。
- 更长范围使用和风 Time Machine 最近 10 天。
- 两者都没有返回时不做本地插值。

## 14. 官方资料

- [v2.6 综合接口](https://docs.caiyunapp.com/weather-api/v2/v2.6/6-weather.html)
- [实况数据](https://docs.caiyunapp.com/weather-api/v2/v2.6/1-realtime.html)
- [小时级预报](https://docs.caiyunapp.com/weather-api/v2/v2.6/3-hourly.html)
- [天级预报](https://docs.caiyunapp.com/weather-api/v2/v2.6/4-daily.html)
- [v2 预警](https://docs.caiyunapp.com/weather-api/v2/v2.6/5-alert.html)
- [生活指数对照](https://docs.caiyunapp.com/weather-api/v2/v2.6/tables/lifeindex.html)
- [单位制](https://docs.caiyunapp.com/weather-api/v2/v2.6/tables/unit.html)
- [数据覆盖](https://docs.caiyunapp.com/weather-api/v2/v2.6/tables/coverage.html)
- [版本说明](https://docs.caiyunapp.com/weather-api/version-guide.html)
- [MCP Server](https://docs.caiyunapp.com/weather-api/mcp.html)
