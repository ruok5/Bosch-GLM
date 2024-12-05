
#ifndef PREFS_H
#define PREFS_H

struct prefs_t {
  float neutralPositionMag;
  float triggerPositionMag;
};

void clearPrefsInEEPROM();
void dumpPrefs(prefs_t& p);
void writePrefsToEEPROM(prefs_t& p);
bool readPrefsFromEEPROM(prefs_t& p);

#endif  // PREFS_H
