; Read only the current GUI attempt's log. The PS driver preserves an old log
; before starting Hermes-Setup, so earlier success/failure cannot decide this run.
BootstrapTerminalState(path) {
    if (path = "" or !FileExist(path))
        return { kind: "pending", line: "" }
    try {
        f := FileOpen(path, "r-d") ; share reads and writes with the installer
        if !f
            return { kind: "pending", line: "" }
        try content := f.Read()
        finally f.Close()
    } catch {
        return { kind: "pending", line: "" } ; contention is no observation
    }
    complete := ""
    for line in StrSplit(content, "`n", "`r") {
        ; This is the installer's terminal event, not npm stderr or a retry log.
        if RegExMatch(line, "^(?:\S+\s+)?ERROR\s+[^\r\n]*\bbootstrap FAILED(?:\s|$)")
            return { kind: "failed", line: line }
        if InStr(line, "bootstrap complete")
            complete := line
    }
    return { kind: complete != "" ? "complete" : "pending", line: complete }
}

RequireBootstrapProgress(path) {
    terminal := BootstrapTerminalState(path)
    if terminal.kind = "failed"
        throw Error("installer reported terminal failure: " terminal.line)
    return terminal.kind = "complete"
}
