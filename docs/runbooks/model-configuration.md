# 本地模型配置

入口位于“设置 / 模型配置”。后端在 `/api/draftloop/v1/settings/models` 提供读取与版本化保存，`/discover` 读取模型列表，`/test` 验证一次 JSON 返回。只允许当前本地工作区所有者调用；沿用浏览器会话、Origin/Host 与客户端请求头边界。

模型列表是账户实时可访问的列表；无法读取时允许手动输入模型 ID。自定义服务需要提供 OpenAI Chat Completions 兼容接口。不同供应商结构化输出、视觉与参数支持有差异，声明能力并不等于已经验证全部任务。

密钥保存于工作区数据目录 `model-profiles.json`，权限 600，原子替换。前端只收到 `hasApiKey`。未传新密钥且地址未变时保留现有密钥；地址变化后清空。不要提交这个私有运行文件到版本库。活跃任务读取独立配置快照。

各预设依据以下官方说明核对，核对日期 2026-09-12：

- [OpenAI 模型列表](https://developers.openai.com/api/reference/resources/models/methods/list)
- [Anthropic OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)：兼容层部分参数被忽略，包括 response_format；本应用仍校验实际返回 JSON。
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [DeepSeek API](https://api-docs.deepseek.com/)
- [阿里云百炼 OpenAI 兼容](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
- [OpenRouter API](https://openrouter.ai/docs/api_reference/overview)
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- [LM Studio OpenAI compatibility](https://lmstudio.ai/docs/developer/openai-compat)

连接测试不会批改真实作文，也不证明模型的评分能力。小作文所选模型需要图像输入；RAG 审核、重评、报告教学可单独选择模型。默认跟随对应写作任务。

2026-09-13 补充核对的预设：

- [智谱 HTTP API](https://docs.bigmodel.cn/cn/guide/develop/http/introduction)
- [Moonshot 官方 Kimi Code 接口配置](https://moonshotai.github.io/kimi-code/en/configuration/providers)
- [xAI REST API](https://api.x.ai/docs/)
- [Mistral 兼容迁移指南](https://docs.mistral.ai/resources/migration-guides)
- [Groq OpenAI 兼容](https://console.groq.com/docs/openai)
- [硅基流动快速上手](https://docs.siliconflow.cn/docs/userguide/quickstart)

预设覆盖常用服务商；并不宣称每个商业模型都向当前账户开放。OpenRouter、硅基流动及自定义兼容服务可接入更多供应商的模型，列表以服务端实时返回为准。
