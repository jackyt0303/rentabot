"""
Terminal UI utilities — colored output for agent activity.
Ported from course project (Munder Difflin) with rental-domain adaptations.
"""


class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Agent colors
    ORCHESTRATOR = "\033[94m"   # Bright blue
    FINANCE = "\033[92m"       # Bright green
    REPORT = "\033[93m"        # Bright yellow
    ERROR = "\033[91m"         # Bright red
    WARNING = "\033[33m"       # Yellow/orange
    SUCCESS = "\033[92m"       # Bright green
    INFO = "\033[96m"          # Bright cyan

    # Backgrounds
    BG_BLUE = "\033[44m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"


def agent_print(agent_name: str, message: str, color: str = Colors.RESET):
    """Print a message with agent-specific color coding."""
    icon_map = {
        "ORCHESTRATOR": "🎯",
        "FINANCE": "💰",
        "REPORT": "📊",
        "SYSTEM": "⚙️",
        "ERROR": "❌",
        "SUCCESS": "✅",
        "CONFIRM": "❓",
    }
    icon = icon_map.get(agent_name, "•")
    label = f"{color}{Colors.BOLD}[{agent_name}]{Colors.RESET}"
    print(f"  {icon} {label} {color}{message}{Colors.RESET}")


def print_header(text: str, width: int = 60):
    """Print a styled header box."""
    border = "═" * width
    padding = (width - len(text) - 2) // 2
    centered = f" {'·' * padding} {text} {'·' * padding} "
    print(f"\n{Colors.BOLD}{Colors.INFO}╔{border}╗")
    print(f"║{centered[:width]}║")
    print(f"╚{border}╝{Colors.RESET}")


def print_balance(label: str, amount: float):
    """Print a formatted MYR balance line."""
    formatted = f"RM {amount:,.2f}"
    agent_print("SYSTEM", f"{label}: {formatted}", Colors.INFO)
