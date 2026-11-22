// The 8 zones around the car and the gaze regions (the 3 front zones share the gaze region "front") in words, for the Simulation log
// (narrator.ts) and the driver display (hud.ts). "away" (none of them) is handled where it is used.

export type Arrow = "left" | "right" | "ahead" | "behind" | "behind-left" | "behind-right";

interface Region {
  zone: string; // "... appeared in the <zone>"
  short: string; // the statistics table
  look: string; // "The driver looks at <look>"
  title: string; // driver display: what to do
  place: string; // driver display: "A car is <place>"
  arrow: Arrow;
}

export const REGIONS: Record<string, Region> = {
  // gaze region only (never a zone): looking at the road = the whole windshield, every front_* zone counts
  front: {
    zone: "zone ahead", short: "ahead", look: "the road ahead",
    title: "Watch ahead", place: "ahead of you", arrow: "ahead",
  },
  front_left: {
    zone: "front-left zone", short: "front-left", look: "the road, front left",
    title: "Watch ahead left", place: "ahead on the left", arrow: "left",
  },
  front_center: {
    zone: "zone straight ahead", short: "ahead", look: "the road straight ahead",
    title: "Obstacle ahead", place: "right in front of you", arrow: "ahead",
  },
  front_right: {
    zone: "front-right zone", short: "front-right", look: "the road, front right",
    title: "Watch ahead right", place: "ahead on the right", arrow: "right",
  },
  side_left: {
    zone: "left blind spot", short: "left blind spot", look: "out of the left side window",
    title: "Look left", place: "in your left blind spot", arrow: "left",
  },
  side_right: {
    zone: "right blind spot", short: "right blind spot", look: "out of the right side window",
    title: "Look right", place: "in your right blind spot", arrow: "right",
  },
  mirror_left: {
    zone: "left mirror zone", short: "left mirror", look: "the left mirror",
    title: "Check the left mirror", place: "behind you on the left", arrow: "behind-left",
  },
  mirror_center: {
    zone: "rear-view mirror zone", short: "rear mirror", look: "the rear-view mirror",
    title: "Check the rear-view mirror", place: "right behind you", arrow: "behind",
  },
  mirror_right: {
    zone: "right mirror zone", short: "right mirror", look: "the right mirror",
    title: "Check the right mirror", place: "behind you on the right", arrow: "behind-right",
  },
};
