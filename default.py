import sys


def main(args=None):
    args = list(sys.argv[1:] if args is None else args)
    first = args[0] if args else ''
    # Preserve Kodi weather refreshes and the existing location-settings actions.
    if first.isdigit() or (first.startswith('loc') and first[3:].isdigit()):
        from lib import weather, config
        config.init(cache=True)
        weather.Main(first)
    else:
        from dashboard import main as open_dashboard
        open_dashboard(args)


if __name__ == '__main__':
    main()
