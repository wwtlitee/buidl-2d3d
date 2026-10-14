# 第三方组件与模型许可

本项目自有代码按 [MIT](LICENSE) 发布。下列第三方组件、下载的模型、运行时与驱动继续适用它们自己的许可，不能因为放在同一个工作目录就视为 MIT。

## 随源码附带的组件

| 组件 | 用途 | 保留的许可 |
| --- | --- | --- |
| [Google model-viewer 4.3.1](https://github.com/google/model-viewer) | 本地 GLB 预览 | [Apache-2.0](visual/vendor/model-viewer.LICENSE)，源包版本与校验值见 [frontend-dependencies.json](config/frontend-dependencies.json) |
| [TripoSR](https://github.com/VAST-AI-Research/TripoSR) | 可选轻量 3D 路线 | 发布包内部 `engines/triposr/src/LICENSE`：MIT，版权属于 Tripo AI 与 Stability AI |
| TripoSR 中的 Transformer 组件 | 该路线的模型实现 | 保留各源文件的 Apache-2.0 声明；许可证全文同时位于 [model-viewer.LICENSE](visual/vendor/model-viewer.LICENSE) |
| torchmcubes 适配代码 | TripoSR 网格提取 | 发布包内部 `engines/triposr/torchmcubes/LICENSE`：MPL-2.0；相应源文件随包保留 |

TripoSR 源码包位于 [config/deployment-bundles/triposr-0.5.0.zip](config/deployment-bundles/triposr-0.5.0.zip)，未打包模型权重或 Python 环境。应用的模型安装过程会解出原许可。README 中的界面截图来自无用户数据的本地空白环境；首页雕塑封面是本项目生成的装饰图，不能用来说明本机模型效果。

## 安装时另行下载

Qwen、TRELLIS / trellis.cpp、Pixal3D、SPAR3D、MV-Adapter、图片编辑模型、Python 包、CUDA 组件及其他可选运行器均来自配置注明的官方来源。用户应在模型管理中阅读对应许可和使用条件，再决定是否安装；有些模型需要在 Hugging Face 接受许可并提供只读令牌。

配置文件中的下载 URL、版本和哈希用于确认来源，并不替代下载内容的许可。MIT 许可不涵盖这些权重、第三方软件或其输出的所有可能用途。

## 本公开版未分发

- Hunyuan、TripoSG 和 PartCrafter 的既有打包源码涉及额外组件或分发条件，本版不附带这些源码包。依赖它们的安装候选会直接说明公开版缺少该资源，不能一键安装。
- 已退役的视频、绑骨与动作流程不开放新创作或安装；旧 MIA / Auto-Rig Pro 等包未随源码公开。
- 本机权重、生成作品、上传素材、缓存、运行环境、令牌与安装/推理日志未随仓库公开。

以上排除不代表对第三方项目本身作出许可评价。需要这些路线时，请直接查阅原项目的当前许可与安装说明，并自行确认使用条件。
