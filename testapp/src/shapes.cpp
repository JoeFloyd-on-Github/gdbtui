#include "shapes.h"

int Shape::next_id = 1;

Shape::Shape(const std::string &n) : name(n), id(next_id++)
{
}

Shape::~Shape()
{
}

Rect::Rect(Point a, Point b) : Shape("rect"), tl(a), br(b)
{
}

double Rect::area() const
{
    return (br.x - tl.x) * (br.y - tl.y);
}

Circle::Circle(Point c, double r) : Shape("circle"), center(c), radius(r)
{
}

double Circle::area() const
{
    return 3.14159265358979 * radius * radius;
}
