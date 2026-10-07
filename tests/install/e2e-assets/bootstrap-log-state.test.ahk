#Requires AutoHotkey v2.0
#SingleInstance Force
#Include bootstrap-log-state.ahk

Check(condition, message) {
    if !condition
        throw Error(message)
}
root := A_Args[1]
DirCreate(root)
log := root "\bootstrap-installer.log"
try {
    Check(!RequireBootstrapProgress(log), "missing log is pending")
    FileAppend("INFO bootstrap complete`nERROR bootstrap FAILED stage=products error=old`n", log)
    FileMove(log, root "\prior-attempt.log")
    Check(!RequireBootstrapProgress(log), "preserved prior attempt cannot decide current run")
    FileAppend("INFO manifest received`nWARN stderr: ERROR bootstrap FAILED in child output`n", log)
    ; The actual terminal marker starts with ERROR; child stderr is never one.
    Check(!RequireBootstrapProgress(log), "progress and stderr remain pending")
    FileAppend("INFO hermes_bootstrap_lib::bootstrap: bootstrap complete install_root=fixture`n", log)
    FileAppend("2026-10-07T14:48:25.859Z ERROR hermes_bootstrap_lib::bootstrap: bootstrap FAILED stage=products error=probe timeout`n", log)
    failed := false
    try RequireBootstrapProgress(log)
    catch Error as err {
        failed := InStr(err.Message, "stage=products") && InStr(err.Message, "probe timeout")
    }
    Check(failed, "current failure is immediate and contextual")
    FileDelete(log)
    FileAppend("INFO hermes_bootstrap_lib::bootstrap: bootstrap complete install_root=fixture`n", log)
    Check(RequireBootstrapProgress(log), "current completion is retained")
    Check(FileExist(root "\prior-attempt.log"), "old evidence is retained")
    FileAppend("bootstrap log state tests passed`n", root "\result.txt")
    try FileAppend("bootstrap log state tests passed`n", '*')
} catch Error as err {
    try FileAppend(err.Message "`n", root "\error.txt")
    try FileAppend(err.Message "`n", '**')
    ExitApp(1)
}
ExitApp(0)
