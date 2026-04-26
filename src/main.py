"""Compatibility entrypoint used by the managed Dockerfile."""

from oidc_hunter.app import main

raise SystemExit(main())
