# SAM builds the custom distribution without pulling in the personal gateway.
build-PooledApiFunction:
	python3 -m pip install --no-deps --target "$(ARTIFACTS_DIR)" .
	python3 -c 'import subprocess, sys, tomllib; deps = tomllib.load(open("pyproject.toml", "rb"))["project"]["optional-dependencies"]["saas"]; subprocess.run([sys.executable, "-m", "pip", "install", "--target", sys.argv[1], *deps], check=True)' "$(ARTIFACTS_DIR)"

build-ControlApiFunction: build-PooledApiFunction

build-KnowledgeApiFunction: build-PooledApiFunction
