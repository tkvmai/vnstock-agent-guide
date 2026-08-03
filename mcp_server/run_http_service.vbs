' Launch the shared vnstock MCP service with no visible console window.
' Used by the "vnstock-mcp-http" logon scheduled task so the service runs
' invisibly in the background. 0 = hidden, False = don't wait for exit.
CreateObject("WScript.Shell").Run """" & _
  CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & _
  "\run_http_service.cmd""", 0, False
