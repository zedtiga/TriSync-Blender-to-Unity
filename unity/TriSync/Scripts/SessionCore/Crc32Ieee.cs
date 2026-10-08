using System;
using System.Globalization;
using System.IO;

namespace BlenderSyncVNext.SessionCore
{
    internal static class Crc32Ieee
    {
        private const uint Polynomial = 0xedb88320u;
        private static readonly uint[] Table = BuildTable();

        public static string ComputeHex(byte[] data)
        {
            var crc = 0xffffffffu;
            var bytes = data ?? Array.Empty<byte>();
            for (var i = 0; i < bytes.Length; i++)
                crc = Update(crc, bytes[i]);
            return Format(crc);
        }

        public static string ComputeStreamHex(Stream stream)
        {
            if (stream == null)
                throw new ArgumentNullException(nameof(stream));

            var crc = 0xffffffffu;
            var buffer = new byte[64 * 1024];
            int read;
            while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
            {
                for (var i = 0; i < read; i++)
                    crc = Update(crc, buffer[i]);
            }
            return Format(crc);
        }

        private static uint Update(uint crc, byte value)
        {
            return Table[(crc ^ value) & 0xff] ^ (crc >> 8);
        }

        private static string Format(uint crc)
        {
            return (~crc).ToString("x8", CultureInfo.InvariantCulture);
        }

        private static uint[] BuildTable()
        {
            var table = new uint[256];
            for (var index = 0; index < table.Length; index++)
            {
                var value = (uint)index;
                for (var bit = 0; bit < 8; bit++)
                    value = (value & 1) != 0 ? Polynomial ^ (value >> 1) : value >> 1;
                table[index] = value;
            }
            return table;
        }
    }
}
