# Minimal echo server for \\.\pipe\attila_ai — answers every line with ACK.
# Run (from WSL): powershell.exe -NoProfile -ExecutionPolicy Bypass -File tools/pipe_server.ps1
$name = 'attila_ai'
Write-Output "pipe server up on \\.\pipe\$name"
while ($true) {
    $pipe = New-Object System.IO.Pipes.NamedPipeServerStream($name, [System.IO.Pipes.PipeDirection]::InOut, 1)
    $pipe.WaitForConnection()
    try {
        $reader = New-Object System.IO.StreamReader($pipe)
        $writer = New-Object System.IO.StreamWriter($pipe)
        $writer.AutoFlush = $true
        while ($null -ne ($line = $reader.ReadLine())) {
            Write-Output "recv: $line"
            $writer.WriteLine("ACK: $line")
        }
    } catch {
        Write-Output "client gone: $_"
    }
    $pipe.Dispose()
}
