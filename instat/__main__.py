"""
CLI para o InstaT.

Uso:
  python -m instat extract --profile <id> --type <followers|following> \\
    [--output file.csv] [--engine selenium|playwright|httpx] \\
    [--max-duration N] [--headless|--no-headless] [--proxy-file PATH]

  python -m instat count --profile <id> --type <followers|following>

Credenciais via env vars (preferido):
  INSTAT_USERNAME, INSTAT_PASSWORD
Ou via flags:
  --username U --password P

Formato do --output inferido da extensão:
  .csv → CSVExporter
  .json → JSONExporter
  .db, .sqlite, .sqlite3 → SQLiteExporter

Exit codes:
  0 = sucesso
  1 = input inválido
  2 = erro de autenticação
  3 = erro de extração (todas engines bloqueadas)
  4 = erro inesperado
"""
import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import List, Optional

try:
    from instat.exceptions import (
        AccountBlockedError,
        AllEnginesBlockedError,
        LoginError,
        ProfileNotFoundError,
    )
    from instat.exporters import BaseExporter, CSVExporter, JSONExporter, SQLiteExporter
    from instat.extractor import InstaExtractor
except ImportError:  # pragma: no cover
    from exceptions import (  # type: ignore
        AccountBlockedError,
        AllEnginesBlockedError,
        LoginError,
        ProfileNotFoundError,
    )
    from exporters import (  # type: ignore
        BaseExporter,
        CSVExporter,
        JSONExporter,
        SQLiteExporter,
    )
    from extractor import InstaExtractor  # type: ignore


# --- Exit codes ---
EXIT_OK = 0
EXIT_INPUT = 1
EXIT_AUTH = 2
EXIT_EXTRACT = 3
EXIT_UNEXPECTED = 4


def _resolve_credentials(args) -> tuple:
    """Retorna (username, password).

    Prioridade: env vars > flags > prompt interativo (getpass).
    Flag --password é suportada por retrocompat mas emite warning — argv é
    visível via `ps`/Task Manager. Env var é o caminho preferido.
    Se TTY indisponível e nenhuma fonte tem senha, sai com exit 1.
    """
    username = args.username or os.environ.get('INSTAT_USERNAME')
    password = args.password or os.environ.get('INSTAT_PASSWORD')

    if args.password:
        print(
            "warning: --password on argv is visible to other users via `ps`. "
            "Prefer INSTAT_PASSWORD env var.",
            file=sys.stderr,
        )

    if not username:
        print(
            "error: username missing. Use --username or INSTAT_USERNAME env var.",
            file=sys.stderr,
        )
        sys.exit(EXIT_INPUT)

    if not password:
        if not sys.stdin.isatty():
            print(
                "error: password missing. Set INSTAT_PASSWORD env var "
                "(preferred) or use --password (visible in process list).",
                file=sys.stderr,
            )
            sys.exit(EXIT_INPUT)
        try:
            password = getpass.getpass(f"Instagram password for {username}: ")
        except (EOFError, KeyboardInterrupt):
            print("error: password prompt aborted.", file=sys.stderr)
            sys.exit(EXIT_INPUT)
        if not password:
            print("error: empty password.", file=sys.stderr)
            sys.exit(EXIT_INPUT)

    return username, password


def _exporter_from_output(path: Optional[str],
                          fmt_override: Optional[str]) -> Optional[BaseExporter]:
    """Infere exporter da extensão do arquivo de saída. None se sem output."""
    if not path:
        return None
    fmt = fmt_override
    if not fmt:
        ext = Path(path).suffix.lower().lstrip('.')
        if ext == 'csv':
            fmt = 'csv'
        elif ext == 'json':
            fmt = 'json'
        elif ext in ('db', 'sqlite', 'sqlite3'):
            fmt = 'sqlite'
        else:
            print(
                f"error: cannot infer format from extension '{ext}'. "
                "Use --format csv|json|sqlite.",
                file=sys.stderr,
            )
            sys.exit(EXIT_INPUT)
    if fmt == 'csv':
        return CSVExporter(path)
    if fmt == 'json':
        return JSONExporter(path)
    if fmt == 'sqlite':
        return SQLiteExporter(path)
    print(f"error: unknown format '{fmt}'", file=sys.stderr)
    sys.exit(EXIT_INPUT)


def _load_proxies(path: Optional[str]) -> Optional[List[str]]:
    """Carrega proxies de arquivo (1 por linha). None se não fornecido."""
    if not path:
        return None
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError as e:
        print(f"error: cannot read proxy file: {e}", file=sys.stderr)
        sys.exit(EXIT_INPUT)
    proxies = [line.strip() for line in lines if line.strip()]
    return proxies or None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='instat',
        description='InstaT CLI — Instagram data extraction',
    )
    subs = parser.add_subparsers(dest='command', required=True)

    def add_auth(sp):
        sp.add_argument('--username', help='Instagram username (or INSTAT_USERNAME env)')
        sp.add_argument('--password', help='Instagram password (or INSTAT_PASSWORD env)')

    def add_profile(sp):
        sp.add_argument('--profile', required=True, help='Target profile ID')
        sp.add_argument(
            '--type', required=True, choices=['followers', 'following'],
            help='List type',
        )

    # extract
    e = subs.add_parser('extract', help='Extract followers/following list')
    add_auth(e)
    add_profile(e)
    e.add_argument('--output', help='Output file (.csv, .json, .db/.sqlite)')
    e.add_argument('--format', choices=['csv', 'json', 'sqlite'],
                   help='Override format inferred from extension')
    e.add_argument('--engine', action='append',
                   choices=['selenium', 'playwright', 'httpx'],
                   help='Engine(s) to use. Repeat for multiple (default: selenium)')
    e.add_argument('--max-duration', type=float, default=None,
                   help='Max extraction duration in seconds')
    e.add_argument('--headless', action='store_true', default=True,
                   help='Run browser headless (default)')
    e.add_argument('--no-headless', dest='headless', action='store_false',
                   help='Show browser window')
    e.add_argument('--timeout', type=int, default=10,
                   help='WebDriver timeout in seconds')
    e.add_argument('--proxy-file', help='File with one proxy URL per line')

    # count
    c = subs.add_parser('count', help='Get total count without loading list')
    add_auth(c)
    add_profile(c)
    c.add_argument('--headless', action='store_true', default=True)
    c.add_argument('--no-headless', dest='headless', action='store_false')
    c.add_argument('--timeout', type=int, default=10)

    return parser


def _cmd_extract(args) -> int:
    username, password = _resolve_credentials(args)
    exporter = _exporter_from_output(args.output, args.format)
    proxies = _load_proxies(args.proxy_file)
    engines = args.engine or ['selenium']

    try:
        extractor = InstaExtractor(
            username=username,
            password=password,
            headless=args.headless,
            timeout=args.timeout,
            engines=engines,
            proxies=proxies,
            exporter=exporter,
        )
    except LoginError as e:
        print(f"error: login failed: {e}", file=sys.stderr)
        return EXIT_AUTH

    try:
        if args.type == 'followers':
            result = extractor.get_followers(args.profile, max_duration=args.max_duration)
        else:
            result = extractor.get_following(args.profile, max_duration=args.max_duration)
    except AccountBlockedError as e:
        print(f"error: account blocked: {e}", file=sys.stderr)
        return EXIT_AUTH
    except AllEnginesBlockedError as e:
        print(f"error: all engines blocked: {e}", file=sys.stderr)
        return EXIT_EXTRACT
    except ProfileNotFoundError as e:
        print(f"error: profile not found: {e}", file=sys.stderr)
        return EXIT_EXTRACT
    finally:
        try:
            extractor.quit()
        except Exception:
            pass

    if exporter is None:
        for u in result:
            print(u)
    else:
        print(f"Extracted {len(result)} profiles to {args.output}", file=sys.stderr)
    return EXIT_OK


def _cmd_count(args) -> int:
    username, password = _resolve_credentials(args)
    try:
        extractor = InstaExtractor(
            username=username, password=password,
            headless=args.headless, timeout=args.timeout,
        )
    except LoginError as e:
        print(f"error: login failed: {e}", file=sys.stderr)
        return EXIT_AUTH
    try:
        count = extractor.get_total_count(args.profile, args.type)
    except AccountBlockedError as e:
        print(f"error: account blocked: {e}", file=sys.stderr)
        return EXIT_AUTH
    except ProfileNotFoundError as e:
        print(f"error: profile not found: {e}", file=sys.stderr)
        return EXIT_EXTRACT
    finally:
        try:
            extractor.quit()
        except Exception:
            pass
    if count is None:
        print("error: could not determine count", file=sys.stderr)
        return EXIT_EXTRACT
    print(count)
    return EXIT_OK


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == 'extract':
            return _cmd_extract(args)
        if args.command == 'count':
            return _cmd_count(args)
        parser.print_help()
        return EXIT_INPUT
    except SystemExit:
        raise
    except Exception as e:
        print(f"error: unexpected failure: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == '__main__':
    sys.exit(main())
