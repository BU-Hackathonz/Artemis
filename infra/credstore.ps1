<#
  Windows Credential Manager access for the launcher (dot-sourced by launch.ps1).
  Secrets are stored as generic credentials, persisted for this Windows user only. Windows protects
  them with DPAPI. Nothing here writes a file, and nothing prints a secret.
#>

if (-not ("ArtemisCred" -as [type])) {
    Add-Type -TypeDefinition @"
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
    public static string Read(string target) {
        IntPtr p;
        if (!CredRead(target, 1, 0, out p)) return null;
        try {
            CREDENTIAL c = (CREDENTIAL)Marshal.PtrToStructure(p, typeof(CREDENTIAL));
            if (c.CredentialBlobSize == 0) return "";
            return Marshal.PtrToStringUni(c.CredentialBlob, (int)c.CredentialBlobSize / 2);
        } finally { CredFree(p); }
    }
    public static bool Write(string target, string secret) {
        byte[] bytes = Encoding.Unicode.GetBytes(secret);
        IntPtr blob = Marshal.AllocHGlobal(bytes.Length);
        try {
            Marshal.Copy(bytes, 0, blob, bytes.Length);
            CREDENTIAL c = new CREDENTIAL();
            c.Type = 1; c.TargetName = target; c.CredentialBlobSize = (uint)bytes.Length;
            c.CredentialBlob = blob; c.Persist = 2; c.UserName = "artemis";
            return CredWrite(ref c, 0);
        } finally {
            Array.Clear(bytes, 0, bytes.Length);
            Marshal.Copy(new byte[bytes.Length], 0, blob, bytes.Length);
            Marshal.FreeHGlobal(blob);
        }
    }
    public static bool Delete(string target) { return CredDelete(target, 1, 0); }
}
"@
}

$script:ApiKeyTarget = "Artemis/AnthropicApiKey"
$script:WorkspaceTarget = "Artemis/AnthropicWorkspaceId"

function Get-StoredSecret([string]$Target) { return [ArtemisCred]::Read($Target) }
function Set-StoredSecret([string]$Target, [string]$Value) { return [ArtemisCred]::Write($Target, $Value) }
function Remove-StoredSecret([string]$Target) { return [ArtemisCred]::Delete($Target) }
