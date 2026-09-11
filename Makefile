# SPDX-License-Identifier: Apache-2.0
PREFIX ?= /usr
DESTDIR ?=
BUILD_DIR ?= build
PYTHON ?= python3
CFLAGS ?= -O2

.PHONY: all build install clean dist
all: build

build:
	CC="$(CC)" CFLAGS="$(CFLAGS)" CPPFLAGS="$(CPPFLAGS)" LDFLAGS="$(LDFLAGS)" $(PYTHON) tools/build.py --build-dir "$(BUILD_DIR)"

install:
	$(PYTHON) tools/build.py --build-dir "$(BUILD_DIR)" --install --prefix "$(PREFIX)" --destdir "$(DESTDIR)"

clean:
	$(PYTHON) -c 'import shutil; shutil.rmtree("$(BUILD_DIR)", ignore_errors=True)'

dist:
	$(PYTHON) tools/dist.py
