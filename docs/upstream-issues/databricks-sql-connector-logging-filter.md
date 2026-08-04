# Upstream issue draft: logging filter raises `TypeError` on argless records, breaking MLflow trace export

**Repo:** `databricks/databricks-sql-python`
**Version observed:** `databricks-sql-connector` 4.2.5 (present since 3.4.0, from #341)
**Impact:** Importing `databricks-sql-connector` in a process that also uses
MLflow tracing silently destroys trace export. Traces are created; their spans
never upload and the traces cannot be loaded.

## Summary

On import, the connector attaches a filter to a logger it does not own:

```python
# src/databricks/sql/__init__.py
logging.getLogger("urllib3.connectionpool").addFilter(RedactUrlQueryParamsFilter())
```

`urllib3.connectionpool` is shared by every library in the process that speaks
HTTP through `requests`/`urllib3`, so the filter runs on their records too.

The filter assumes `record.args` is always iterable:

```python
else:
    record.args = tuple(
        (self.redact(arg) if isinstance(arg, str) else arg) for arg in record.args
    )
```

`LogRecord.args` is documented as optional and is `None` on plenty of records —
including any record that an earlier filter has already rendered. Iterating it
raises `TypeError: 'NoneType' object is not iterable`, and because
`Filterer.filter` runs inside `Logger.handle`, the exception does not stay in
the logging subsystem. It propagates out of whatever made the log call.

## What makes it fire in practice

MLflow attaches its own redaction filter to the same logger, and it is installed
first. When it redacts credentials it clears the arguments it has just consumed,
which is correct and legal:

```python
# mlflow/utils/logging_utils.py — SensitiveQueryParamFilter
if redacted != message:
    record.msg = redacted
    record.args = None
```

The connector's filter runs next and raises. The loop then closes on MLflow
itself: it uploads trace span data to blob storage through `requests`, a
transient failure makes `urllib3` log a retry warning containing the presigned
URL, MLflow's filter redacts the credentials in it and nulls the args, the
connector's filter raises, and the `TypeError` propagates out of
`Session.request` and kills the upload.

```
File ".../mlflow/tracing/export/mlflow_v3.py", line 254, in _log_trace
File ".../mlflow/store/artifact/databricks_artifact_repo.py", line 644, in _signed_url_upload_file
File ".../mlflow/utils/request_utils.py", line 282, in _get_http_response_with_retries
    return session.request(method, url, allow_redirects=allow_redirects, **kwargs)
File ".../requests/sessions.py", line 784, in send
File ".../urllib3/connectionpool.py", line 869, in urlopen
    log.warning(
File "/usr/lib/python3.11/logging/__init__.py", line 830, in filter
File ".../databricks/sql/__init__.py", line 43, in filter
    record.args = tuple(
TypeError: 'NoneType' object is not iterable
```

MLflow catches it and logs one line — `Failed to send trace to MLflow backend:
'NoneType' object is not iterable` — so the visible symptom is an empty
experiment rather than a stack trace. Trace metadata is written before the
spans, so the application keeps receiving valid-looking trace IDs for traces
that cannot be loaded. In our case that went unnoticed for a full release.

It only occurs on the retry path, which is why it reproduces in a long-running
service and not in a short script.

## Reproduction

```python
import logging
import databricks.sql                                    # installs the filter
from mlflow.utils.logging_utils import SensitiveQueryParamFilter

log = logging.getLogger("urllib3.connectionpool")
log.addFilter(SensitiveQueryParamFilter())               # MLflow installs this itself
log.addHandler(logging.NullHandler())

url = "https://bucket.s3.amazonaws.com/spans?X-Amz-Signature=deadbeef"
log.warning("Retrying (%r) after connection broken by '%r': %s",
            "Retry(total=4)", "ProtocolError()", url)
# TypeError: 'NoneType' object is not iterable
```

Order matters only in that something must null the args first; the filter is
equally broken on any record logged without arguments:

```python
log.warning("a message with no args")   # same TypeError
```

## Second defect in the same method

The dict branch reads `record.arg[k]` where `record.args[k]` is meant:

```python
if isinstance(record.args, dict):
    for k in record.args.keys():
        record.args[k] = (
            self.redact(record.args[k])
            if isinstance(record.arg[k], str)   # <- typo: record.arg
            else record.args[k]
        )
```

Any record using `%(name)s`-style mapping args raises `AttributeError`, so this
branch cannot ever have executed successfully.

## Suggested fix

```python
def filter(self, record):
    record.msg = self.redact(str(record.msg))
    if isinstance(record.args, dict):
        for k, v in record.args.items():
            record.args[k] = self.redact(v) if isinstance(v, str) else v
    elif record.args:
        record.args = tuple(
            (self.redact(arg) if isinstance(arg, str) else arg) for arg in record.args
        )
    return True
```

`elif record.args` covers both `None` and `()`, and the dict branch loses the
typo. Verified against argless, tuple, mapping and empty-tuple records: all four
render correctly and query parameters are still redacted.

Worth considering separately: a filter installed on `urllib3.connectionpool` at
import time affects every HTTP client in the process, not just the connector's.
Scoping the redaction to the connector's own sessions — or documenting that it
is global — would keep a bug like this from reaching unrelated libraries. As it
stands, two libraries independently doing the right thing produce a failure in
neither's code path.

## Workaround

For anyone hitting this before a release, wrap the method so it tolerates
argless records. Patch the class rather than inserting a filter of your own:
placement cannot help, because the `None` is introduced partway down the chain.

```python
from databricks.sql import RedactUrlQueryParamsFilter

_original = RedactUrlQueryParamsFilter.filter

def _tolerant(self, record):
    if record.args is None:
        record.args = ()
    return _original(self, record)

RedactUrlQueryParamsFilter.filter = _tolerant
```
