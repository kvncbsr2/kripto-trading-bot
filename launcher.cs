using System;
using System.Diagnostics;
using System.IO;
using System.Threading;

namespace KriptoAgentLauncher
{
    class Program
    {
        static Process serverProcess = null;

        static void Main(string[] args)
        {
            Console.OutputEncoding = System.Text.Encoding.UTF8;
            Console.Title = "KRIPTO AGENT — Otonom Kripto Islem Merkezi";

            Console.ForegroundColor = ConsoleColor.Cyan;
            Console.WriteLine("========================================================================");
            Console.WriteLine("          ⚡ KRIPTO AGENT — OTONOM TICARET & KONTROL MERKEZI ⚡         ");
            Console.WriteLine("========================================================================");
            Console.ResetColor();

            string appDir = AppDomain.CurrentDomain.BaseDirectory;
            Directory.SetCurrentDirectory(appDir);

            Console.ForegroundColor = ConsoleColor.Yellow;
            Console.WriteLine("[+] Proje Dizini : " + appDir);
            Console.WriteLine("[+] Port         : 8000");
            Console.WriteLine("[+] Panel Adresi : http://127.0.0.1:8000/dashboard");
            Console.ResetColor();
            Console.WriteLine("------------------------------------------------------------------------");

            string pythonPath = FindPythonPath();
            if (string.IsNullOrEmpty(pythonPath))
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("[HATA] Python calistirilabilir dosyasi bulunamadi!");
                Console.WriteLine("Lutfen Python'in sistemde yuklu ve PATH'e ekli oldugundan emin olun.");
                Console.ResetColor();
                Console.WriteLine("\nCikmak icin bir tusa basin...");
                Console.ReadKey();
                return;
            }

            Console.ForegroundColor = ConsoleColor.Green;
            Console.WriteLine("[+] Python Bulundu: " + pythonPath);
            Console.ResetColor();

            // Set up exit handler
            AppDomain.CurrentDomain.ProcessExit += OnProcessExit;
            Console.CancelKeyPress += (sender, e) =>
            {
                KillServer();
            };

            Console.WriteLine("\n[1/2] Web sunucusu (Uvicorn) baslatiliyor...");

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonPath;
                psi.Arguments = "-m uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000";
                psi.WorkingDirectory = appDir;
                psi.UseShellExecute = false;
                psi.RedirectStandardOutput = false;
                psi.RedirectStandardError = false;

                serverProcess = Process.Start(psi);

                Console.ForegroundColor = ConsoleColor.Green;
                Console.WriteLine("[✓] Sunucu basariyla baslatildi (PID: " + serverProcess.Id + ")");
                Console.ResetColor();

                Console.WriteLine("\n[2/2] Tarayicida kontrol paneli aciliyor...");
                Thread.Sleep(1500);

                try
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "http://127.0.0.1:8000/dashboard",
                        UseShellExecute = true
                    });
                    Console.ForegroundColor = ConsoleColor.Cyan;
                    Console.WriteLine("[✓] Tarayici basariyla acildi: http://127.0.0.1:8000/dashboard");
                    Console.ResetColor();
                }
                catch (Exception ex)
                {
                    Console.WriteLine("[!] Tarayici otomatik acilamadi, lutfen elle girin: " + ex.Message);
                }

                Console.ForegroundColor = ConsoleColor.Yellow;
                Console.WriteLine("\n========================================================================");
                Console.WriteLine("💡 KRIPTO AGENT calisiyor. Paneli kapatmak icin bu pencereyi kapatin.");
                Console.WriteLine("========================================================================\n");
                Console.ResetColor();

                serverProcess.WaitForExit();
            }
            catch (Exception ex)
            {
                Console.ForegroundColor = ConsoleColor.Red;
                Console.WriteLine("[HATA] Sunucu baslatilamadi: " + ex.Message);
                Console.ResetColor();
                Console.WriteLine("\nCikmak icin bir tusa basin...");
                Console.ReadKey();
            }
            finally
            {
                KillServer();
            }
        }

        static string FindPythonPath()
        {
            string[] possiblePaths = new string[]
            {
                "python.exe",
                @"C:\Users\Home\AppData\Local\Programs\Python\Python312\python.exe",
                @"C:\Users\Home\AppData\Local\Programs\Python\Python311\python.exe",
                @"C:\Python312\python.exe",
                @"C:\Python311\python.exe"
            };

            foreach (string p in possiblePaths)
            {
                try
                {
                    Process pTest = new Process();
                    pTest.StartInfo.FileName = p;
                    pTest.StartInfo.Arguments = "--version";
                    pTest.StartInfo.UseShellExecute = false;
                    pTest.StartInfo.RedirectStandardOutput = true;
                    pTest.StartInfo.CreateNoWindow = true;
                    pTest.Start();
                    pTest.WaitForExit(1000);
                    if (pTest.ExitCode == 0)
                    {
                        return p;
                    }
                }
                catch { }
            }

            return null;
        }

        static void OnProcessExit(object sender, EventArgs e)
        {
            KillServer();
        }

        static void KillServer()
        {
            if (serverProcess != null && !serverProcess.HasExited)
            {
                try
                {
                    serverProcess.Kill();
                }
                catch { }
            }
        }
    }
}
