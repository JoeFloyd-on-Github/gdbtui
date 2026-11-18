// A small class hierarchy for exploring objects in Locals / QuickWatch.
#ifndef DEMO_SHAPES_H
#define DEMO_SHAPES_H

#include <string>

struct Point {
    int x;
    int y;
};

class Shape {
public:
    explicit Shape(const std::string &n);
    virtual ~Shape();
    virtual double area() const = 0;
    std::string name;
protected:
    int id;
private:
    static int next_id;
};

class Rect : public Shape {
public:
    Rect(Point a, Point b);
    double area() const override;
    Point tl, br;
};

class Circle : public Shape {
public:
    Circle(Point c, double r);
    double area() const override;
    Point center;
    double radius;
};

#endif
