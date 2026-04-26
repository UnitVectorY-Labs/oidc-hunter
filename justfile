
# Commands for oidc-hunter
default:
  @just --list
# Build oidc-hunter with Python
build:
  python -m compileall .

# Run tests for oidc-hunter with Python
test:
  python -m unittest discover

# Serve documentation locally for testing (bootstraps local Jekyll setup)
docs-serve:
  #!/usr/bin/env bash
  set -euo pipefail
  cd docs

  if [[ ! -f Gemfile ]]; then
    printf '%s\n' \
      'source "https://rubygems.org"' \
      '' \
      'gem "github-pages", group: :jekyll_plugins' \
      'gem "jekyll-remote-theme"' \
      'gem "webrick"' > Gemfile
  fi

  bundle config set --local path vendor/bundle
  bundle check >/dev/null || bundle install
  bundle exec jekyll serve --host 127.0.0.1 --port 4000 --livereload
