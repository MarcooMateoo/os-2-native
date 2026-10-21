# How to extract an OutSystems app

Step-by-step instructions for extracting any OutSystems solution (`.osp`) or
application (`.oap`) with **os2native**, using PowerShell or cmd.

## Before you start

- **Once per machine:** the project is set up (`.venv` exists) and the .NET 10
  runtime is installed. os2native finds a .NET 10 install in your user folder
  (`%LOCALAPPDATA%\Microsoft\dotnet`) automatically, so you don't need to
  change PATH.
- **One subfolder per app** in both `INPUTS` and `OUTPUT`. `OUTPUT\...\extraction`
  is not cleared between runs, so extracting two apps into the same folder
  mixes their modules in the dependency check and the report.

```
os2native\
  INPUTS\
    EmployeeApp\        <- .osp + extra .oml/.xif for EmployeeApp
    MyApp\              <- .osp + extra .oml/.xif for MyApp
  OUTPUT\
    EmployeeApp\        <- results for EmployeeApp
    MyApp\              <- results for MyApp
```

`INPUTS` holds your exports from OutSystems, and os2native never changes it.
`OUTPUT` is rebuilt on every run, so don't hand-edit files there.

## 1. Open PowerShell in the project folder

```powershell
cd "C:\Users\mbmateo\Desktop\Coding Agent Projects\OutSystems_2_Native\os2native"
```

You don't need to activate anything: the commands call `.venv` directly.

## 2. Put the files in the app's INPUTS subfolder

Create `INPUTS\<AppName>\` (for example `INPUTS\MyApp\`) and copy in:

- the `.osp` or `.oap` exported from OutSystems
- any extra `.oml` modules or `.xif` extensions the app depends on, if you
  already know it needs them (step 4 tells you if any are missing)

## 3. Run the extraction

**PowerShell** (takes every file in the folder):

```powershell
.venv\Scripts\os2native package (Get-ChildItem INPUTS\MyApp -File | % FullName) --output OUTPUT\MyApp
```

**cmd** (list the files yourself):

```bat
.venv\Scripts\os2native package INPUTS\MyApp\MyApp.osp INPUTS\MyApp\Extra.oml --output OUTPUT\MyApp
```

Expect about 20–30 seconds per 15 modules. The exit code is 1 if any errors
were logged.

## 4. Check the result

Open these in `OUTPUT\MyApp\`:

| File | What to look for |
|---|---|
| `extraction-issues.log` | **Read first.** The last line gives the error and warning count. **ERROR** = a module or extension failed and is missing from the output. **WARNING** = extraction finished but something is incomplete; check whether it's expected. |
| `dependency-check.json` | Dependencies the package doesn't include, labelled `platform` (expected), `forge` (needs a native replacement) or `unknown` (probably missing from the export). |
| `inventory-details.csv` | Every item with its Service Studio location (open in Excel). Compare it against Service Studio. |
| `inventory-details.md` | The same list, readable as a document, with totals at the top. |
| `inventory.json` | Totals per app and per module, plus the issue counts. |

### Missing dependencies

A log line such as:

```
WARNING  [dependencies] HiQRCoder is used by EmployeeApp, ... but is not in the package ...
```

means the app uses a module or extension that isn't in the export. To fix it:

1. Export it: the `.oml` from Service Studio, or the `.xif` from Integration Studio.
2. Put the file in `INPUTS\MyApp\`.
3. Run step 3 again.

If it's a Forge or platform component that you don't want to extract, add its
name to `scripts\known-external.json` instead.

## 5. Optional: generate the deliverables

```powershell
.venv\Scripts\python scripts\generate_db_schema.py OUTPUT\MyApp
```

```powershell
.venv\Scripts\python scripts\generate_api_catalog.py OUTPUT\MyApp
```

```powershell
.venv\Scripts\python scripts\generate_specs.py OUTPUT\MyApp
```

These write to `OUTPUT\MyApp\deliverables\`: PostgreSQL schema and seed data,
the API catalog, and backend/frontend specs per module.

## 6. Hand it over for validation

Share the output folder (e.g. `OUTPUT\MyApp`), together with anything that
didn't match Service Studio. The reviewer checks the log, the dependency check
and the counts against the raw XML in `OUTPUT\MyApp\_xml\`.

## Tips

- **Close `inventory-details.csv` in Excel before re-running.** If it's open,
  the run writes a timestamped copy (`inventory-details-YYYYMMDD-HHMMSS.csv`)
  and logs a warning.
- **Re-run after adding files:** repeat step 3. The log starts fresh on each
  `package` run.
- **Start completely clean:** delete `OUTPUT\MyApp` first. `INPUTS` is never
  touched.
- **Rebuild only the report** (e.g. after closing Excel), without re-extracting:

  ```powershell
  .venv\Scripts\os2native report OUTPUT\MyApp
  ```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ERROR [Module] OML to XML conversion failed` | .NET 10 is missing or the `.oml` is damaged. Check `dotnet --list-runtimes`, or that `%LOCALAPPDATA%\Microsoft\dotnet` has `Microsoft.NETCore.App 10.x`. |
| `ERROR [input] ...: unsupported file type` | Only `.osp`, `.oap`, `.oml` and `.xif` are accepted. Remove other files from the INPUTS subfolder. |
| `ERROR [input] ...: could not unpack package` | The `.osp`/`.oap` is incomplete or corrupt. Export it again. |
| `.venv\Scripts\os2native` not found | First-time setup is missing. Run `py -m venv .venv`, then `.venv\Scripts\python -m pip install -e ".[dev]"`. |
