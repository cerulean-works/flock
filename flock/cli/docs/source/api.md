# flock.cli.credentials

Finding a Flock server and credential from Python. [How credentials are found](credentials) explains the order. Without a server named, credentials go to `DEFAULT_HOST`, `https://flock-api.cerulean.works`.

[[autodoc]] cli.credentials.resolve

[[autodoc]] cli.credentials.Credentials

[[autodoc]] cli.credentials.auth_headers

[[autodoc]] cli.credentials.load

[[autodoc]] cli.credentials.save

[[autodoc]] cli.credentials.delete

[[autodoc]] cli.credentials.config_dir

[[autodoc]] cli.credentials.credentials_path

[[autodoc]] cli.credentials.normalize_host

