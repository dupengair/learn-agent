#!/usr/bin/env python3
"""
快速开始脚本 - 一键体验 Web Search Agent

后端跟随本目录 .env 自动适配，一份代码两种用法：
- 本地模式（SEARCH_BACKEND=mock + KIMI_BASE_URL 指向本地 Ollama）：
  模型走本地（如 qwen3.5-4b:q4），搜索为内置 mock 样本，验证 ReAct 链路；
- Kimi 模式（MOONSHOT_API_KEY 为真实 Key）：走 Moonshot Formula 真实联网搜索。
"""

import os
import sys
from dotenv import load_dotenv

# 必须在读取任何环境变量之前加载 .env。原版 quickstart 缺这一步，是它
# 「只认 Kimi」的根因之一：config.py 的 load_dotenv 要到 WebSearchAgent
# 构造时才触发，而 key 检查在此之前执行，.env 配置对它不可见。
load_dotenv()

from agent import WebSearchAgent, default_model, is_local_backend
from config import Config

# 彩色输出
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'


def print_colored(text, color):
    """打印彩色文本"""
    print(f"{color}{text}{Colors.END}")


def print_banner():
    """打印欢迎横幅（按当前后端模式显示对应文案）"""
    if is_local_backend():
        backend_line = f"🤖 Web Search Agent - 快速体验（本地模式）"
        model_line = f"模型: {default_model()} (Ollama)"
        desc_line = "搜索结果来自内置 mock 样本，验证 ReAct 链路（不联网）"
    else:
        backend_line = "🤖 Web Search Agent - 快速体验（Kimi 模式）"
        model_line = "模型: kimi-k3"
        desc_line = "通过 Moonshot Formula 在服务端真实联网搜索"
    banner = f"""
╔══════════════════════════════════════════════════════════╗
║  {backend_line}
║                                                          ║
║  {model_line}
║  {desc_line}
╚══════════════════════════════════════════════════════════╝
"""
    print_colored(banner, Colors.CYAN)


def check_api_key():
    """检查 API Key 配置（按后端模式区分要求）

    本地模式：key 只是占位符——本地服务不校验内容，但 OpenAI SDK 要求
    非空，缺失时自动补 "ollama"，不再强制交互输入。
    Kimi 模式：保留原有申请引导。
    """
    api_key = os.getenv("MOONSHOT_API_KEY")
    if not api_key:
        # 向后兼容：尝试旧的环境变量名
        api_key = os.getenv("KIMI_API_KEY")

    if not api_key and is_local_backend():
        print_colored("ℹ️  本地模式未检测到 key，自动使用占位值（本地服务不校验）", Colors.CYAN)
        return "ollama"

    if not api_key:
        print_colored("\n⚠️  未检测到 API Key", Colors.WARNING)
        print("\n请按以下步骤配置:")
        print("1. 访问 https://platform.moonshot.ai/ 获取 API Key")
        print("2. 设置环境变量:")
        print("   export MOONSHOT_API_KEY='your-api-key'")
        print("   (或使用: export KIMI_API_KEY='your-api-key')")
        print("\n或者直接输入 API Key (输入 'skip' 跳过):")

        user_input = input("> ").strip()

        if user_input.lower() == 'skip':
            return None
        elif user_input:
            return user_input
        else:
            return None

    print_colored("✅ API Key 已配置", Colors.GREEN)
    return api_key


def demo_search(agent):
    """演示搜索功能（按后端模式给出合适的问题集）"""
    print_colored("\n📝 演示搜索功能", Colors.HEADER)
    print("-" * 60)

    if is_local_backend():
        # mock 后端返回固定样本，时效性问题拿不到真实信息，演示重点
        # 是「想→做→看」链路本身；换不依赖外部信息的问题。
        demo_questions = [
            "什么是 ReAct 循环？",
            "月球的直径是多少公里？",
            "用一句话解释什么是上下文工程",
        ]
    else:
        # 真实联网搜索：时效性问题正好展示搜索价值。
        demo_questions = [
            "OpenAI 最新发布了什么产品？",
            "2024年有哪些重要的AI突破？",
            "如何开始学习机器学习？",
        ]
    
    print("选择一个演示问题，或输入您自己的问题:")
    for i, q in enumerate(demo_questions, 1):
        print(f"{i}. {q}")
    print("0. 输入自定义问题")
    
    choice = input("\n请选择 (0-3): ").strip()
    
    try:
        choice = int(choice)
        if choice == 0:
            question = input("请输入您的问题: ").strip()
            if not question:
                print_colored("❌ 问题不能为空", Colors.FAIL)
                return
        elif 1 <= choice <= len(demo_questions):
            question = demo_questions[choice - 1]
        else:
            print_colored("❌ 无效的选择", Colors.FAIL)
            return
    except ValueError:
        print_colored("❌ 请输入数字", Colors.FAIL)
        return
    
    print_colored(f"\n🔍 正在搜索: {question}", Colors.BLUE)
    print("请稍候，Agent 正在搜索和分析...")
    print("-" * 60)
    
    try:
        answer = agent.search_and_answer(question)
        print_colored("\n📖 Agent 回答:", Colors.GREEN)
        print(answer)
    except Exception as e:
        print_colored(f"\n❌ 搜索失败: {str(e)}", Colors.FAIL)


def interactive_mode(agent):
    """交互模式"""
    print_colored("\n💬 进入交互模式", Colors.HEADER)
    print("您可以连续提问，输入 'quit' 退出")
    print("-" * 60)
    
    while True:
        question = input("\n您的问题: ").strip()
        
        if question.lower() in ['quit', 'exit', 'q']:
            print_colored("👋 感谢使用！", Colors.GREEN)
            break
        
        if not question:
            continue
        
        print_colored("🔍 搜索中...", Colors.BLUE)
        
        try:
            answer = agent.search_and_answer(question)
            print_colored("\n📖 回答:", Colors.GREEN)
            print(answer)
        except Exception as e:
            print_colored(f"❌ 错误: {str(e)}", Colors.FAIL)


def main():
    """主函数"""
    print_banner()
    
    # 检查 API Key
    api_key = check_api_key()
    if not api_key:
        print_colored("\n⚠️  无法继续，需要配置 API Key", Colors.WARNING)
        sys.exit(1)
    
    # 创建 Agent：显式传 base_url 与 model。原版只传 api_key，会落到
    # __init__ 签名的硬编码默认值（moonshot.cn + kimi-k3），.env 配置
    # 对端点和模型都不生效——这正是它无法直接用于本地 Ollama 的原因。
    try:
        print_colored("\n🚀 初始化 Agent...", Colors.BLUE)
        model = default_model()
        agent = WebSearchAgent(
            api_key=api_key,
            base_url=Config.KIMI_BASE_URL,
            model=model,
        )
        print_colored(f"✅ Agent 已就绪（{model} @ {Config.KIMI_BASE_URL}）", Colors.GREEN)
    except Exception as e:
        print_colored(f"❌ 初始化失败: {str(e)}", Colors.FAIL)
        sys.exit(1)
    
    # 选择模式
    print("\n选择使用模式:")
    print("1. 演示搜索 (快速体验)")
    print("2. 交互模式 (连续对话)")
    print("3. 退出")
    
    mode = input("\n请选择 (1-3): ").strip()
    
    if mode == "1":
        demo_search(agent)
        # 询问是否继续
        cont = input("\n是否进入交互模式？(y/n): ").strip().lower()
        if cont == 'y':
            interactive_mode(agent)
    elif mode == "2":
        interactive_mode(agent)
    elif mode == "3":
        print_colored("👋 再见！", Colors.GREEN)
    else:
        print_colored("❌ 无效的选择", Colors.FAIL)
    
    print_colored(f"\n感谢使用 Web Search Agent（{default_model()}）！", Colors.CYAN)
    print("更多功能请查看:")
    print("- README.md: 完整文档")
    print("- examples.py: 高级示例")
    print("- main.py: 主程序")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print_colored("\n\n👋 程序被中断", Colors.WARNING)
    except Exception as e:
        print_colored(f"\n❌ 发生错误: {str(e)}", Colors.FAIL)
        sys.exit(1)
