// energyplus.exe for DesignBuilder's bundled EnergyPlus engine.
//
// DesignBuilder ships EnergyPlus only as energyplusapi.dll (no command-line program, no ReadVarsESO).
// This program gives that DLL the usual `energyplus [options] input.idf` command line, so that eppy,
// EnergyPlus-MCP and our scripts can use it unchanged. When -r / --readvars is given it also writes the
// CSV files ReadVarsESO would write (<prefix>.csv from the .eso and <prefix>Meter.csv from the .mtr).
//
// Built by tools/dbengine/setup_engine.py with the C# compiler that comes with Windows (.NET Framework 4).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Text;

static class Program
{
    [DllImport("energyplusapi.dll", CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr stateNew();

    [DllImport("energyplusapi.dll", CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr energyPlusVersion();

    [DllImport("energyplusapi.dll", CallingConvention = CallingConvention.Cdecl)]
    static extern int energyplus(IntPtr state, int argc,
        [MarshalAs(UnmanagedType.LPArray, ArraySubType = UnmanagedType.LPStr)] string[] argv);

    static int Main(string[] args)
    {
        if (args.Length == 1 && (args[0] == "-v" || args[0] == "--version"))
        {   // the DLL ignores --version, so answer it here in the same format as energyplus.exe
            Console.WriteLine(Marshal.PtrToStringAnsi(energyPlusVersion()));
            return 0;
        }
        bool readvars = false;
        string outDir = ".", prefix = "eplus", suffix = "L";
        var pass = new List<string> { "energyplus" };
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            if (a == "-r" || a == "--readvars") { readvars = true; continue; }
            if ((a == "-d" || a == "--output-directory") && i + 1 < args.Length) outDir = args[i + 1];
            if ((a == "-p" || a == "--output-prefix") && i + 1 < args.Length) prefix = args[i + 1];
            if ((a == "-s" || a == "--output-suffix") && i + 1 < args.Length) suffix = args[i + 1];
            pass.Add(a);
        }
        int rc = energyplus(stateNew(), pass.Count, pass.ToArray());
        if (rc == 0 && readvars)
        {
            try { ReadVars(outDir, prefix, suffix); }
            catch (Exception e) { Console.Error.WriteLine("readvars: " + e.Message); return 1; }
        }
        return rc;
    }

    // Output file names for the three suffix styles (EnergyPlus --output-suffix L / C / D).
    static void ReadVars(string dir, string prefix, string suffix)
    {
        string eso, mtr, csv, mcsv;
        if (suffix == "C") { eso = prefix + ".eso"; mtr = prefix + ".mtr"; csv = prefix + ".csv"; mcsv = prefix + "Meter.csv"; }
        else if (suffix == "D") { eso = prefix + "-out.eso"; mtr = prefix + "-out.mtr"; csv = prefix + "-out.csv"; mcsv = prefix + "-mtr.csv"; }
        else { eso = prefix + "out.eso"; mtr = prefix + "out.mtr"; csv = prefix + "out.csv"; mcsv = prefix + "mtr.csv"; }
        Convert(Path.Combine(dir, eso), Path.Combine(dir, csv));
        Convert(Path.Combine(dir, mtr), Path.Combine(dir, mcsv));
    }

    static readonly string[] Freq = { "", "", "Hourly", "Daily", "Monthly", "RunPeriod", "Annual" };
    static readonly string[] Months = { "", "January", "February", "March", "April", "May", "June", "July",
                                        "August", "September", "October", "November", "December" };

    // ESO / MTR -> CSV in ReadVarsESO's layout: "Date/Time" then one column per reported variable,
    // header "Key:Name [Unit](Frequency)", one row per time stamp that has values.
    static void Convert(string src, string dst)
    {
        if (!File.Exists(src)) return;
        var lines = File.ReadAllLines(src);
        var cols = new Dictionary<string, int>();
        var header = new List<string> { "Date/Time" };
        int k = 0;
        for (; k < lines.Length; k++)
        {
            string ln = lines[k];
            if (ln.StartsWith("End of Data Dictionary")) { k++; break; }
            var p = ln.Split(new[] { ',' }, 3);
            int id;
            if (p.Length < 3 || !int.TryParse(p[0], out id) || id <= 6) continue;
            string rest = p[2];
            int bang = rest.IndexOf('!');
            string name = (bang >= 0 ? rest.Substring(0, bang) : rest).Trim();
            string freq = bang >= 0 ? rest.Substring(bang + 1).Trim().Split(' ')[0] : "";
            if (freq == "Each") freq = "Each Call";
            cols[p[0]] = header.Count;
            header.Add(name.Replace(",", ":") + "(" + freq + ")");
        }
        var sb = new StringBuilder();
        sb.AppendLine(string.Join(",", header));
        string[] row = null;
        string stamp = "";
        Action flush = () =>
        {
            if (row != null && row.Skip(1).Any(v => v != null))
            {
                row[0] = stamp;
                sb.AppendLine(string.Join(",", row.Select(v => v ?? "")));
            }
            row = null;
        };
        for (; k < lines.Length; k++)
        {
            string ln = lines[k];
            if (ln.StartsWith("End of Data")) break;
            int c = ln.IndexOf(',');
            if (c < 0) continue;
            string id = ln.Substring(0, c);
            var f = ln.Split(',');
            int n;
            if (int.TryParse(id, out n) && n >= 1 && n <= 6)
            {
                flush();
                if (n == 1) continue;  // environment title
                row = new string[header.Count];
                if (n == 2 && f.Length >= 8)
                {
                    int hour = int.Parse(f[5].Trim());
                    double end = double.Parse(f[7].Trim(), System.Globalization.CultureInfo.InvariantCulture);
                    int h = end >= 60 ? hour : hour - 1, m = end >= 60 ? 0 : (int)end;
                    stamp = string.Format(" {0:00}/{1:00}  {2:00}:{3:00}:00", int.Parse(f[2]), int.Parse(f[3]), h, m);
                }
                else if (n == 3 && f.Length >= 4) stamp = string.Format(" {0:00}/{1:00}", int.Parse(f[2]), int.Parse(f[3]));
                else if (n == 4 && f.Length >= 3) stamp = Months[int.Parse(f[2])];
                else if (n == 5) stamp = "simdays=" + (f.Length > 1 ? f[1].Trim() : "");
                else stamp = "annual";
                continue;
            }
            int col;
            if (row != null && cols.TryGetValue(id, out col) && f.Length > 1) row[col] = f[1].Trim();
        }
        flush();
        File.WriteAllText(dst, sb.ToString());
    }
}
