import os
import sys

from paper_downloader.cli import setup_logging
from paper_downloader.orchestrator import Orchestrator

def main():
    """
    一键运行的主函数入口，专门方便通过 Vscode 点击右上角“运行”按钮直接执行。
    """
    # 初始化日志打印格式
    setup_logging("INFO")
    
    print("="*60)
    print("🚀 正在启动全自动文献下载程序 (Vscode 快捷运行模式)")
    print("="*60)
    
    # 默认从 input/papers.csv 读取 DOI 列表
    csv_path = "input/papers.csv"
    
    if not os.path.exists(csv_path):
        print(f"❌ 找不到输入文件: {csv_path}。请确保在这个地方放了包含 DOI 的列表。")
        sys.exit(1)
        
    try:
        # 直接调用底层的编排器（绕过终端的命令行参数传递）
        orch = Orchestrator(
            csv_path=csv_path,
            config_path=None,   # 采用默认的 config/settings.yaml
            project_root=None   # 自动锁定当前文件夹为运行根目录
        )
        
        # 开始下载
        orch.run()
        
    except KeyboardInterrupt:
        print("\n⚠️ 你手动终止了程序。")
    except Exception as e:
        print(f"\n❌ 程序运行出错: {e}")

if __name__ == "__main__":
    main()
