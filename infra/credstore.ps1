<#
  Windows Credential Manager access for the launcher (dot-sourced by launch.ps1).
  Secrets are stored as generic credentials, persisted for this Windows user only. Windows protects
  them with DPAPI. Nothing here writes a file, and nothing prints a secret.

  If the C# helper cannot be compiled (AppLocker, antivirus, Constrained Language Mode), the
  launcher must still start: $script:CredStoreAvailable is $false and every function degrades to
  "nothing saved" so the caller falls back to typing the key.
#>

$script:CredStoreAvailable = $true
$script:CredStoreError = ""
try {
    if (-not ("ArtemisCred" -as [type])) {
        Add-Type -ErrorAction Stop -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class ArtemisCred {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct CREDENTIAL {
        public uint Flags; public uint Type; public string TargetName; public string Comment;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
        public uint CredentialBlobSize; public IntPtr CredentialBlob; public uint Persist;
        public uint AttributeCount; public IntPtr Attributes; public string TargetAlias; public string UserName;
    }
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredRead(string target, uint type, uint flags, out IntPtr cred);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredWrite(ref CREDENTIAL cred, uint flags);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool CredDelete(string target, uint type, uint flags);
    [DllImport("advapi32.dll")]
    static extern void CredFree(IntPtr buffer);

    // CRED_TYPE_GENERIC = 1, CRED_PERSIST_LOCAL_MACHINE = 2 (this user, this machine).
    // Write and Delete return 0 on success, otherwise the Win32 error (1168 = not found).
    public static string Read(string target) {
        IntPtr p;
        if (!CredRead(target, 1, 0, out p)) return null;
        try {
            CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
            if (c.CredentialBlobSize == 0) return "";
            return Marshal.PtrToStringUni(c.CredentialBlob, (int)c.CredentialBlobSize / 2);
        } finally { CredFree(p); }
    }
    public static int Write(string target, string secret) {
        byte[] bytes = Encoding.Unicode.GetBytes(secret);
        IntPtr blob = Marshal.AllocHGlobal(bytes.Length);
        try {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            CREDENTIAL c = new CREDENTIAL();
            c.Type = 1; c.TargetName = target; c.CredentialBlobSize = (uint)bytes.Length;
            c.CredentialBlob = blob; c.Persist = 2; c.UserName = "artemis";
            return CredWrite(ref c, 0) ? 0 : Marshal.GetLastWin32Error();
        } finally {
            Array.Clear(bytes, 0, bytes.Length);
            Marshal.Copy(new byte[bytes.Length], 0, blob, bytes.Length);
            Marshal.FreeHGlobal(blob);
        }
    }
    public static int Delete(string target) {
        return CredDelete(target, 1, 0) ? 0 : Marshal.GetLastWin32Error();
    }
}
"@
    }
} catch {
    $script:CredStoreAvailable = $false
    $script:CredStoreError = $_.Exception.Message
}

$script:ApiKeyTarget = "Artemis/AnthropicApiKey"
$script:WorkspaceTarget = "Artemis/AnthropicWorkspaceId"

# Returns the saved value, or $null when nothing is saved or the store is unavailable.
function Get-StoredSecret([string]$Target) {
    if (-not $script:CredStoreAvailable) { return $null }
    try { return [ArtemisCred]::Read($Target) } catch { return $null }
}

# Returns $true only when Windows confirmed the write; otherwise $false and $script:CredStoreError.
function Set-StoredSecret([string]$Target, [string]$Value) {
    if (-not $script:CredStoreAvailable) { return $false }
    try { $rc = [ArtemisCred]::Write($Target, $Value) } catch { $script:CredStoreError = $_.Exception.Message; return $false }
    if ($rc -eq 0) { return $true }
    $script:CredStoreError = "Windows error $rc"
    return $false
}

# Returns "removed", "none" (nothing was saved: Windows error 1168) or "failed".
function Remove-StoredSecret([string]$Target) {
    if (-not $script:CredStoreAvailable) { return "failed" }
    try { $rc = [ArtemisCred]::Delete($Target) } catch { $script:CredStoreError = $_.Exception.Message; return "failed" }
    if ($rc -eq 0) { return "removed" }
    if ($rc -eq 1168) { return "none" }
    $script:CredStoreError = "Windows error $rc"
    return "failed"
}
