"""Единый запуск приложения. Анализ главы 1: python -m chapter1.main."""

import argparse


def main():
    from chapter3.app import create_app
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    # Локальный однопользовательский режим. Отладчик и внешний доступ выключены.
    create_app().run(host="127.0.0.1", port=args.port, debug=False)
