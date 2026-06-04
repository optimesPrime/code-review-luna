def ask(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    try:
        answer = input(f"{prompt} {hint} ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    if answer == "":
        return default
    return answer in ("y", "yes")
