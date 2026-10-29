# Mirror of resolve_update_ref: change a real Desktop source input in NEXT,
# without moving the driver's checkout, refs or index. No install/build runs here.
function New-NextCommit([string]$Repo, [string]$Parent) {
    $marker = Join-Path $WorkRoot "next-marker.txt"
    Set-Content -LiteralPath $marker -Encoding ASCII -Value "synthetic next commit for the HEAD -> NEXT install E2E leg"
    $blob = Invoke-Git @("-C", $Repo, "hash-object", "-w", "--no-filters", $marker)
    $desktopInput = "apps/desktop/electron/app-version.ts"
    $desktop = Join-Path $WorkRoot "next-desktop-input.ts"
    $content = Invoke-Git @("-C", $Repo, "show", "$($Parent):$desktopInput")
    [System.IO.File]::WriteAllText($desktop, "$content`n// Synthetic NEXT Desktop input for install/update E2E.`n", [System.Text.UTF8Encoding]::new($false))
    $desktopBlob = Invoke-Git @("-C", $Repo, "hash-object", "-w", "--no-filters", $desktop)
    $saved = @{}
    $vars = @{
        GIT_INDEX_FILE = (Join-Path $WorkRoot "next.index")
        GIT_AUTHOR_NAME = "Hermes E2E"; GIT_AUTHOR_EMAIL = "e2e@hermes.invalid"
        GIT_COMMITTER_NAME = "Hermes E2E"; GIT_COMMITTER_EMAIL = "e2e@hermes.invalid"
    }
    foreach ($k in $vars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k); [Environment]::SetEnvironmentVariable($k, $vars[$k]) }
    try {
        Invoke-Git @("-C", $Repo, "read-tree", $Parent) | Out-Null
        Invoke-Git @("-C", $Repo, "update-index", "--add", "--cacheinfo", "100644,$blob,.hermes-e2e-next") | Out-Null
        Invoke-Git @("-C", $Repo, "update-index", "--cacheinfo", "100644,$desktopBlob,$desktopInput") | Out-Null
        $tree = Invoke-Git @("-C", $Repo, "write-tree")
        return Invoke-Git @("-C", $Repo, "commit-tree", $tree, "-p", $Parent, "-m", "e2e: synthetic next commit")
    } finally {
        foreach ($k in $vars.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
        Remove-Item -LiteralPath $vars.GIT_INDEX_FILE -Force -ErrorAction SilentlyContinue
    }
}
