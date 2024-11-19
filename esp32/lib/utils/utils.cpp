#include "stdio.h"
#include "HardwareSerial.h"

uint16_t hexDumpCounter = 0;

void hexDump(const void* object, long size) {
  hexDumpCounter++;
  unsigned int i;
  const unsigned char* const px = (unsigned char*)object;
  char buf[130];
  size_t offset = 0;

  for (i = 0; i < size; ++i) {
    if (i % (sizeof(int) * 8) == 0) {
      if (offset) {  // offset from previous lines
        Serial.printf("[%u] %s\n", hexDumpCounter, buf);
      }
      offset = sprintf(buf, "%08X     ", i);  // offset
    } else if (i % 4 == 0) {
      offset += sprintf(buf + offset, "  ");  // whitespace every 4 bytes
    }
    offset += sprintf(buf + offset, "%02X ", px[i]);  // hex code of byte
  }

  if (offset) {  // remaining offset
    Serial.printf("%u %s\n", hexDumpCounter, buf);
  }
}
