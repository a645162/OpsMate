"""运维助手（OpsAssistant）。

一个本地优先的服务器运维台：把常用的“看一眼、连上去、改一下”压缩到最短路径。
当前版本以蓝队云（landui.com）为第一个接入的云厂商，通过第三方 SDK
``landui-sdk`` 调用其控制台接口（非官方，与蓝队云无隶属关系）。
"""

APP_NAME = "OpsAssistant"
APP_NAME_ZH = "运维助手"
__version__ = "0.1.0"

__all__ = ["APP_NAME", "APP_NAME_ZH", "__version__"]
