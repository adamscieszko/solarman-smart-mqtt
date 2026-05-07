#!/bin/sh
exec python run.py -d -i "${INTERVAL:-600}" "$@"