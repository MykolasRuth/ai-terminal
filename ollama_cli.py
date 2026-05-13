import sys

from ai_terminal.cli import main


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "web":
        from ai_terminal.web_server import main as web_main

        web_main()
    else:
        sys.exit(main(sys.argv[1:]))
